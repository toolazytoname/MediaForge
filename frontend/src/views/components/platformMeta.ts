// 平台展示元数据（纯前端展示用：简称 + 品牌色 + 品牌 logo）。
// logo 取自 simple-icons（CC0-1.0，https://simpleicons.org），只是图标路径数据，
// 不 vendor 也不重发平台官方美术资源；抖音/今日头条/视频号无对应品牌图标条目，
// 保留 icon: undefined，UI 回退为文字色块。
// 「已支持」= 后端 pipeline/config.py::PlatformsConfig 真实注册的 5 个平台。
// 「规划中」= 蚁小二对标计划（docs/research/yixiaoer-teardown-and-plan.md）里列出、
// 本项目尚未实现发布适配器的平台位——仅作陈列，不可点击授权。
import {
  siXiaohongshu,
  siWechat,
  siX,
  siSinaweibo,
  siKuaishou,
  siBilibili,
  siZhihu,
  siTiktok,
} from 'simple-icons'

export interface PlatformMeta {
  key: string
  label: string
  color: string
  group: 'domestic' | 'intl'
  iconPath?: string
}

export const SUPPORTED_PLATFORMS: readonly PlatformMeta[] = [
  { key: 'toutiao', label: '头条', color: '#ff6600', group: 'domestic' },
  { key: 'xiaohongshu', label: '小红书', color: `#${siXiaohongshu.hex}`, group: 'domestic', iconPath: siXiaohongshu.path },
  { key: 'douyin', label: '抖音', color: '#1f1f1f', group: 'domestic', iconPath: siTiktok.path },
  { key: 'wechat_mp', label: '公众号', color: `#${siWechat.hex}`, group: 'domestic', iconPath: siWechat.path },
  { key: 'x', label: 'X', color: `#${siX.hex}`, group: 'intl', iconPath: siX.path },
]

export const PLANNED_PLATFORMS: readonly PlatformMeta[] = [
  { key: 'weibo', label: '微博', color: `#${siSinaweibo.hex}`, group: 'domestic', iconPath: siSinaweibo.path },
  { key: 'kuaishou', label: '快手', color: `#${siKuaishou.hex}`, group: 'domestic', iconPath: siKuaishou.path },
  { key: 'shipinhao', label: '视频号', color: '#576b95', group: 'domestic' },
  { key: 'bilibili', label: 'B站', color: `#${siBilibili.hex}`, group: 'domestic', iconPath: siBilibili.path },
  { key: 'zhihu', label: '知乎', color: `#${siZhihu.hex}`, group: 'domestic', iconPath: siZhihu.path },
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
