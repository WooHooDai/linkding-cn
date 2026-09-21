// 标签候选匹配打分：分值越低越靠前。
// 规则：
//   - name（标签名本身，汉字/英文）支持前缀 + 任意位置子串匹配；
//   - pinyin_full / pinyin_first（拼音）只支持前缀匹配。
// 拼音不开放子串匹配的原因：拼音子串（如单个字母 "i"）会命中几乎所有
// 中文标签的全拼/首字母，候选列表噪音爆炸；汉字子串是有语义的（用户记得
// 标签中间的某个字），噪音可控。
// 注意：本函数是纯函数，只做匹配打分，不做排序。最终顺序由调用方
// 按分值稳定排序后，层内保持 tag-cache 的频次降序。
export function scoreTag(tag, search) {
  const name = tag.name.toLowerCase();
  if (name.startsWith(search)) return 0; // 标签名前缀命中（最强信号）
  if (tag.pinyin_full && tag.pinyin_full.startsWith(search)) return 1; // 全拼前缀
  if (tag.pinyin_first && tag.pinyin_first.startsWith(search)) return 2; // 首字母前缀
  if (name.includes(search)) return 3; // 标签名任意位置子串命中
  return -1; // 不匹配
}
