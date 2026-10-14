/** Markdown 渲染配置。整个应用共用一个实例。 */

// 用 /lib/common 而不是全量包：只带约 35 种常用语言，
// 全量包会把所有语言的高亮规则都打进 bundle（体积差约 4 倍）。
import hljs from 'highlight.js/lib/common'
import MarkdownIt from 'markdown-it'

// 代码高亮的主题样式。
import 'highlight.js/styles/github.css'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export const markdown = new MarkdownIt({
  // 关键：模型输出属于**不可信内容**，必须禁止原始 HTML，否则形如
  // <img onerror=...> 的回答会直接变成 XSS。
  html: false,
  linkify: true,
  breaks: true,
  highlight(code: string, language: string): string {
    if (language && hljs.getLanguage(language)) {
      try {
        const highlighted = hljs.highlight(code, { language, ignoreIllegals: true }).value
        return `<pre class="hljs"><code>${highlighted}</code></pre>`
      } catch {
        // 高亮失败时落到下面的纯文本分支，不影响渲染。
      }
    }
    return `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`
  },
})

export function renderMarkdown(source: string): string {
  return markdown.render(source ?? '')
}
