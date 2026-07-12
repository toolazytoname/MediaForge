// 平台展示元数据（纯前端展示用：简称 + 品牌色近似值）。
// 不引入外部 logo 资源（无授权品牌 SVG），用文字色块规避版权/资产缺失问题。
// 「已支持」= 后端 pipeline/config.py::PlatformsConfig 真实注册的 5 个平台。
// 「规划中」= 蚁小二对标计划（docs/research/yixiaoer-teardown-and-plan.md）里列出、
// 本项目尚未实现发布适配器的平台位——仅作陈列，不可点击授权。

export interface PlatformMeta {
  key: string
  label: string
  color: string
  group: 'domestic' | 'intl'
}

export const SUPPORTED_PLATFORMS: readonly PlatformMeta[] = [
  { key: 'toutiao', label: '头条', color: '#ff6600', group: 'domestic' },
  { key: 'xiaohongshu', label: '小红书', color: '#ff2442', group: 'domestic' },
  { key: 'douyin', label: '抖音', color: '#1f1f1f', group: 'domestic' },
  { key: 'wechat_mp', label: '公众号', color: '#07c160', group: 'domestic' },
  { key: 'x', label: 'X', color: '#14171a', group: 'intl' },
]

export const PLANNED_PLATFORMS: readonly PlatformMeta[] = [
  { key: 'weibo', label: '微博', color: '#e6162d', group: 'domestic' },
  { key: 'kuaishou', label: '快手', color: '#fe3666', group: 'domestic' },
  { key: 'shipinhao', label: '视频号', color: '#576b95', group: 'domestic' },
  { key: 'bilibili', label: 'B站', color: '#00a1d6', group: 'domestic' },
  { key: 'zhihu', label: '知乎', color: '#0084ff', group: 'domestic' },
]

const BY_KEY: ReadonlyMap<string, PlatformMeta> = new Map(
  [...SUPPORTED_PLATFORMS, ...PLANNED_PLATFORMS].map((p) => [p.key, p]),
)

export function platformMeta(key: string): PlatformMeta {
  return (
    BY_KEY.get(key) ?? {
      key,
      label: key.slice(0, 2),
      color: '#8c8c8c',
      group: 'domestic',
    }
  )
}
