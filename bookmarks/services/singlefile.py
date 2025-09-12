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
        return []

    if not custom_options:
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
    ublock_options = shlex.split(settings.LD_SINGLEFILE_UBLOCK_OPTIONS)
    global_options = shlex.split(settings.LD_SINGLEFILE_OPTIONS)    # 环境变量参数
    custom_options = get_custom_options(config)    # 自定义配置文件参数
    options = custom_options or global_options      # 若自定义配置文件有参数，则直接抛弃环境变量中的参数

    # 默认参数：需过滤掉不可重复参数
    default_options = [
        '--browser-arg=--disable-blink-features=AutomationControlled'
    ]
    need_default_options = {
        "--user-agent": True
    }
    for option in options:
        arg_name = option.split("=")[0]
        if need_default_options.get(arg_name):
            need_default_options[arg_name] = False

    if need_default_options["--user-agent"]:
        default_options.append(f'--user-agent={settings.LD_DEFAULT_USER_AGENT}')


    # 参数优先级：default_options > custom_options > global_options
    args = [singlefile_path] + default_options + ublock_options + options + [url, filepath]

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
