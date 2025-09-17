import logging
import os
import shlex
import signal
import subprocess

from django.conf import settings


class SingleFileError(Exception):
    pass


logger = logging.getLogger(__name__)


def get_custom_options(config: dict):
    if config:
        custom_options = config.get("singlefile_args")
    else:
        logger.debug(f"未提供自定义配置")
        return []

    if not custom_options:
        logger.debug(f"未提供【singlefile_args】参数")
        return []

    args = []
    
    if isinstance(custom_options, dict):
        for arg, value in custom_options.items():
            args.append(arg + "=" + value)
    else:
        logger.error("Fail to get singlefile's option, please check settings' format.")
        return []

    logger.debug(f"singlefile自定义参数为：{args}")
    return args

def create_snapshot(url: str, filepath: str, config: dict = None):
    singlefile_path = settings.LD_SINGLEFILE_PATH

    # 解析参数
    custom_options = get_custom_options(config)    # 自定义配置文件参数
    global_options = shlex.split(settings.LD_SINGLEFILE_OPTIONS)    # 环境变量参数
    ublock_options = shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS)
    required_options = [
        '--browser-arg=--disable-blink-features=AutomationControlled',
        f'--user-agent={settings.LD_DEFAULT_USER_AGENT}'
    ]   # 必需参数

    # 参数去重，优先级：custom_options > global_options > ublock_options
    multi_value_arg_list = [ # 允许多个值的参数
        "--browser-script",
        "--browser-stylesheet",
        "--browser-arg",
        "--browser-cookie",
        "--crawl-rewrite-rule",
        "--emulate-media-feature",
        "--http-header"
    ]

    def merge_option(target_options, merged_options):
        ''' 越早添加，优先级越高（参数越靠后） '''
        target_options_arg_list = list(map(lambda option: option.split("=")[0], target_options))
        for merged_option in merged_options:
            if (merged_option in target_options):   # 跳过相同参数
                continue
            merged_arg, merged_value = merged_option.split('=',1)
            if merged_arg in multi_value_arg_list: # 允许多个值的参数直接添加
                target_options.insert(0, merged_option)
            if merged_arg not in target_options_arg_list: # 之前没有的参数直接添加
                target_options.insert(0, merged_option)
        
        return target_options
    
    # 优先级：custom > global > ublock > required
    result_options = []
    result_options = merge_option(result_options, custom_options)
    result_options = merge_option(result_options, global_options)
    result_options = merge_option(result_options, ublock_options)
    result_options = merge_option(result_options, required_options)

    args = [singlefile_path] + result_options + [url, filepath]

    logger.debug(f"singlefile最终完整参数为: {args}")

    try:
        # Use start_new_session=True to create a new process group
        process = subprocess.Popen(args, start_new_session=True)
        process.wait(timeout=settings.LD_SINGLEFILE_TIMEOUT_SEC)

        # check if the file was created
        if not os.path.exists(filepath):
            raise SingleFileError("Failed to create snapshot")
    except subprocess.TimeoutExpired:
        # First try to terminate properly
        try:
            logger.error(
                "Timeout expired while creating snapshot. Terminating process..."
            )
            process.terminate()
            process.wait(timeout=20)
            raise SingleFileError("Timeout expired while creating snapshot")
        except subprocess.TimeoutExpired:
            # Kill the whole process group, which should also clean up any chromium
            # processes spawned by single-file
            logger.error("Timeout expired while terminating. Killing process...")
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            raise SingleFileError("Timeout expired while creating snapshot")
    except subprocess.CalledProcessError as error:
        raise SingleFileError(f"Failed to create snapshot: {error.stderr}")
