import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/** Markdown → 消毒后的 HTML，供 v-html 渲染实时预览用（防止内容被注入脚本）。 */
export function renderMarkdown(src: string): string {
  return DOMPurify.sanitize(md.render(src))
}
