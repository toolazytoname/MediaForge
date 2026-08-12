<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  ArrowLeftOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CommentOutlined,
  FilePdfOutlined,
  FileTextOutlined,
  LinkOutlined,
  PictureOutlined,
  PlusOutlined,
  ReadOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'

type Screen = 'start' | 'loading' | 'article' | 'diff' | 'final' | 'delivery'
type Reference = { id: string; kind: '图片' | '链接' | 'PDF' | 'Markdown'; name: string; state: 'ready' | 'failed' }

const screen = ref<Screen>('start')
const topic = ref('为什么你每天都在用 AI，却没有因此早点下班？')
const idea = ref('我想从自己做 MediaForge 的失败说起。工具和流程越多，人反而越不知道从哪里开始。真正需要的不是更多功能，而是一条能把事情做完的路径。')
const refs = ref<Reference[]>([])
const comment = ref('减少说教感，保留“我把流程做复杂后自己不会用”的真实失败。')
const selectedText = ref(false)
const selectedExcerpt = ref('')
const selectionMenu = ref<{ visible: boolean; x: number; y: number }>({ visible: false, x: 0, y: 0 })
const commentInput = ref<HTMLTextAreaElement | null>(null)
const noteSaved = ref(false)
const generationStep = ref(0)
const account = ref('公众号 · 普通人的 AI 实验室')
const deliveryMode = ref('原生定时')

const sourceOptions: ReadonlyArray<Omit<Reference, 'id' | 'state'>> = [
  { kind: '图片', name: '书桌与跑步鞋.jpg' },
  { kind: '链接', name: 'https://example.com/ai-workflow' },
  { kind: 'PDF', name: '我的产品复盘.pdf' },
  { kind: 'Markdown', name: '零散想法.md' },
]

const sourceIcons = { 图片: PictureOutlined, 链接: LinkOutlined, PDF: FilePdfOutlined, Markdown: FileTextOutlined }
const readyCount = computed(() => refs.value.filter((item) => item.state === 'ready').length)

function addReference(source: Omit<Reference, 'id' | 'state'>): void {
  if (refs.value.some((item) => item.name === source.name)) return
  refs.value = [...refs.value, { ...source, id: `${source.kind}-${Date.now()}`, state: source.kind === '链接' ? 'failed' : 'ready' }]
}

function removeReference(id: string): void {
  refs.value = refs.value.filter((item) => item.id !== id)
}

function startGeneration(): void {
  if (!topic.value.trim() && !idea.value.trim()) return
  screen.value = 'loading'
  generationStep.value = 0
  window.setTimeout(() => { generationStep.value = 1 }, 500)
  window.setTimeout(() => { generationStep.value = 2 }, 1050)
  window.setTimeout(() => { screen.value = 'article' }, 1550)
}

function saveComment(): void {
  noteSaved.value = true
  selectionMenu.value.visible = false
}

function captureTextSelection(event: MouseEvent): void {
  const selection = window.getSelection()?.toString().trim() ?? ''
  if (!selection) {
    selectionMenu.value.visible = false
    return
  }
  selectedExcerpt.value = selection
  selectionMenu.value = { visible: true, x: event.clientX, y: event.clientY }
}

function openContextComment(event: MouseEvent): void {
  const selection = window.getSelection()?.toString().trim() ?? ''
  if (!selection) return
  selectedExcerpt.value = selection
  selectionMenu.value = { visible: true, x: event.clientX, y: event.clientY }
}

function openSelectedComment(): void {
  if (!selectedExcerpt.value) return
  selectedText.value = true
  selectionMenu.value.visible = false
  nextTick(() => commentInput.value?.focus())
}

function openDiff(): void {
  screen.value = 'diff'
}
</script>

<template>
  <main class="creator-prototype" aria-label="MediaForge 文章优先原型">
    <header class="topbar">
      <button class="wordmark" type="button" @click="screen = 'start'">MediaForge <span>创作</span></button>
      <div v-if="screen !== 'start'" class="article-status"><span class="status-light"></span> {{ screen === 'loading' ? '正在生成草稿' : '文章草稿' }}</div>
      <button class="quiet-link" type="button">自动化创作 <span>稍后再设</span></button>
    </header>

    <section v-if="screen === 'start'" class="start-view">
      <div class="start-copy">
        <p class="kicker">从一个想法开始</p>
        <h1>把你想说的，做成一篇完整的图文文章。</h1>
        <p>先写主题和想法。资料、格式和平台都可以随后补充。</p>
      </div>

      <section class="composer" aria-label="开始创作">
        <p class="composer-heading">主题和想法</p>
        <label for="topic">主题</label>
        <a-input id="topic" v-model:value="topic" size="large" placeholder="例如：为什么每天用 AI，却没有早点下班？" />
        <label for="idea">你的想法</label>
        <a-textarea id="idea" v-model:value="idea" :auto-size="{ minRows: 6, maxRows: 10 }" placeholder="写下你亲历的事、判断、矛盾或希望读者带走的东西。" />

        <div class="reference-row">
          <div>
            <span class="field-label">可选参考资料</span>
            <span class="field-help">图片、链接、PDF 或 Markdown。坏资料不会阻塞起稿。</span>
          </div>
          <a-dropdown>
            <button class="add-reference" type="button"><PlusOutlined /> 添加参考资料</button>
            <template #overlay>
              <a-menu>
                <a-menu-item v-for="item in sourceOptions" :key="item.name" @click="addReference(item)">
                  <component :is="sourceIcons[item.kind]" /> {{ item.kind }}：{{ item.name }}
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </div>
        <div v-if="refs.length" class="references" aria-live="polite">
          <div v-for="reference in refs" :key="reference.id" class="reference-item" :class="reference.state">
            <component :is="sourceIcons[reference.kind]" />
            <span>{{ reference.name }}</span>
            <small v-if="reference.state === 'failed'">资料暂时无法读取，本文不会引用它</small>
            <small v-else>已准备</small>
            <button type="button" :aria-label="`移除 ${reference.name}`" @click="removeReference(reference.id)">×</button>
          </div>
        </div>
        <div class="composer-footer">
          <span>{{ readyCount ? `${readyCount} 份资料会一起参考` : '不用资料也可以直接开始' }}</span>
          <a-button type="primary" size="large" :disabled="!topic.trim() && !idea.trim()" @click="startGeneration">生成文章 <ReadOutlined /></a-button>
        </div>
      </section>
      <p class="draft-note">这一步只生成草稿，不会发布，也不会覆盖你之后手动改的内容。</p>
    </section>

    <section v-else-if="screen === 'loading'" class="loading-view" aria-live="polite">
      <div class="loading-card">
        <div class="paper-orbit"><FileTextOutlined /></div>
        <p class="kicker">正在为你起草</p>
        <h1>{{ topic || '正在整理你的想法' }}</h1>
        <div class="generation-steps">
          <p :class="{ done: generationStep >= 0 }"><CheckOutlined /> 整理你的观点和资料</p>
          <p :class="{ done: generationStep >= 1 }"><CheckOutlined /> 写出有个人视角的文章</p>
          <p :class="{ done: generationStep >= 2 }"><CheckOutlined /> 生成封面与上下文配图</p>
        </div>
        <button class="quiet-link" type="button" @click="screen = 'start'"><ArrowLeftOutlined /> 返回继续补充</button>
      </div>
    </section>

    <section v-else-if="screen === 'article'" class="article-view">
      <aside class="article-rail">
        <button class="rail-back" type="button" @click="screen = 'start'"><ArrowLeftOutlined /> 重新输入</button>
        <div class="rail-section"><strong>这篇文章</strong><span>草稿 · 未发布</span></div>
        <button class="rail-action" type="button" @click="selectedText = true"><CommentOutlined /> 给这一段提意见</button>
        <button class="rail-action" type="button" @click="openDiff"><FileTextOutlined /> 查看修改对比</button>
        <button class="rail-action" type="button" @click="screen = 'final'"><CheckOutlined /> 看最终文章</button>
      </aside>
      <article class="article-paper" @mouseup="captureTextSelection" @contextmenu.prevent="openContextComment">
        <div class="article-meta">个人创作 · 草稿</div>
        <h1>为什么你每天都在用 AI，却没有因此早点下班？</h1>
        <p class="dek">我把一条“全自动内容流水线”做得越来越完整，最后却发现，自己根本不会用它。</p>
        <figure class="cover-art"><div class="cover-sun"></div><div class="cover-desk"></div><figcaption>封面图：工作不是缺工具，而是缺一条能走完的路。</figcaption></figure>
        <p>我最近做了一个尴尬的实验：给自己做一个内容创作系统。它能找题、查资料、写稿、配图、导出、准备发布。每补一个能力，我都觉得离“自动化”更近了一点。</p>
        <p class="selection-hint">选中一段文字后，可以直接评论。</p>
        <p class="selectable" :class="{ selected: selectedText }">可真正坐到电脑前时，我还是停在了第一步：我有一个想法，但不知道该点哪一个按钮。原来我搭建的不是一张工作台，而是一条需要先理解说明书的流水线。</p>
        <div v-if="selectedText" class="inline-comment" role="dialog" aria-label="针对段落提出意见">
          <span>评论所选内容</span>
          <blockquote v-if="selectedExcerpt">{{ selectedExcerpt }}</blockquote>
          <a-textarea ref="commentInput" v-model:value="comment" :auto-size="{ minRows: 2, maxRows: 4 }" />
          <div><button type="button" @click="selectedText = false">取消</button><a-button type="primary" size="small" @click="saveComment">保存意见</a-button></div>
        </div>
        <p>这不是 AI 的问题。它反而提醒我，普通人需要的不是再多一个提示词库，而是一条从“我想做什么”开始、能把事情做完的路径。AI 最好的位置，也许不是替你决定要说什么，而是接住你已经有的判断。</p>
        <figure class="inline-art"><div class="path-line"></div><div class="path-person">你</div><figcaption>插图：从想法到文章，中间不该是一排陌生的系统状态。</figcaption></figure>
        <p>所以这次我决定反过来做：打开就是一篇文章。先把真实想法写进去，等文章出来，再对着具体的段落说“这里不对”。过程可以复杂，但不该成为创作者的负担。</p>
        <div v-if="noteSaved" class="saved-note"><CheckOutlined /> 已保存一条局部意见。文章正文还没有被改动。</div>
        <button
          v-if="selectionMenu.visible"
          class="selection-menu"
          type="button"
          :style="{ left: `${selectionMenu.x}px`, top: `${selectionMenu.y}px` }"
          @click.stop="openSelectedComment"
        ><CommentOutlined /> 评论所选内容</button>
        <span class="sr-only">右键评论也可打开这一操作。</span>
      </article>
      <aside class="comment-panel">
        <p class="kicker">意见</p>
        <h2>让 AI 改什么？</h2>
        <p>可以对整篇文章说，也可以先点文章中的一段。</p>
        <a-textarea v-model:value="comment" :auto-size="{ minRows: 5, maxRows: 8 }" />
        <a-button block type="primary" @click="openDiff">生成修改提案</a-button>
        <p class="panel-note">只生成提案。确认前，原文不会改变。</p>
      </aside>
    </section>

    <section v-else-if="screen === 'diff'" class="diff-view">
      <header class="diff-header"><button class="rail-back" type="button" @click="screen = 'article'"><ArrowLeftOutlined /> 回到文章</button><div><p class="kicker">修改提案</p><h1>这一次，AI 建议这样改。</h1></div><span class="scope-badge">整篇 + 1 段批注</span></header>
      <div class="diff-grid">
        <article class="diff-paper before"><div class="diff-label">原文</div><h2>为什么你每天都在用 AI，却没有因此早点下班？</h2><p>可真正坐到电脑前时，我还是停在了第一步：我有一个想法，但不知道该点哪一个按钮。</p><p class="removed">原来我搭建的不是一张工作台，而是一条需要先理解说明书的流水线。</p></article>
        <article class="diff-paper after"><div class="diff-label">建议稿</div><h2>为什么你每天都在用 AI，却没有因此早点下班？</h2><p>可真正坐到电脑前时，我还是停在了第一步：我有一个想法，却不知道该从哪里开始。</p><p class="added">我把每一种可能都做成了入口，最后把最重要的事藏了起来：先把一个想法写成一篇人愿意读完的文章。</p></article>
      </div>
      <footer class="diff-footer"><p><CommentOutlined /> 依据你的意见：{{ comment }}</p><div><button type="button" class="text-button" @click="screen = 'article'">拒绝这次修改</button><a-button type="primary" size="large" @click="screen = 'final'">确认这版 <CheckOutlined /></a-button></div></footer>
    </section>

    <section v-else-if="screen === 'final'" class="final-view">
      <header class="final-header"><div><p class="kicker">最终阅读</p><h1>文章已经是你确认过的样子。</h1></div><div><button class="text-button" type="button" @click="screen = 'article'">继续编辑</button><a-button type="primary" @click="screen = 'delivery'">选择发布方式 <SendOutlined /></a-button></div></header>
      <article class="final-paper"><p class="article-meta">普通人的 AI 实验室</p><h1>为什么你每天都在用 AI，却没有因此早点下班？</h1><p class="dek">工具越多，越该先问：它到底帮我完成了什么？</p><div class="final-cover"></div><p>我把每一种可能都做成了入口，最后把最重要的事藏了起来：先把一个想法写成一篇人愿意读完的文章。</p><p>AI 最好的位置，不是替你决定要说什么，而是接住你已经有的判断。</p><p>这次我决定反过来做：打开就是一篇文章。过程可以复杂，但不该成为创作者的负担。</p></article>
      <button class="markdown-link" type="button"><FileTextOutlined /> 导出 Markdown</button>
    </section>

    <section v-else class="delivery-view">
      <header class="delivery-header"><button class="rail-back" type="button" @click="screen = 'final'"><ArrowLeftOutlined /> 返回最终文章</button><p class="kicker">交付前确认</p><h1>选择账号与发布方式。</h1><p>账号和平台能力会清楚显示。这里不会创建本地定时任务。</p></header>
      <div class="delivery-layout">
        <section class="delivery-card"><label for="account">账号</label><a-select id="account" v-model:value="account" size="large" style="width: 100%"><a-select-option value="公众号 · 普通人的 AI 实验室">公众号 · 普通人的 AI 实验室</a-select-option><a-select-option value="头条号 · 普通人的 AI 实验室">头条号 · 普通人的 AI 实验室</a-select-option></a-select><div class="channel-note"><ReadOutlined /> {{ account.startsWith('公众号') ? '公众号：图文、封面，支持平台原生定时。' : '头条：图文、封面，暂以草稿交付。' }}</div><label>发布方式</label><a-radio-group v-model:value="deliveryMode" class="delivery-radios"><a-radio value="原生定时"><ClockCircleOutlined /> 平台原生定时</a-radio><a-radio value="立即交付">立即交付到平台草稿</a-radio></a-radio-group><div v-if="deliveryMode === '原生定时'" class="schedule-box"><label for="schedule-time">平台原生定时</label><a-date-picker id="schedule-time" show-time style="width: 100%" /><p>时间会交给平台保存和执行，不由 MediaForge 在本地触发发布。</p></div><a-button type="primary" size="large" block>准备 {{ deliveryMode === '原生定时' ? '平台定时' : '平台草稿' }} <SendOutlined /></a-button><p class="safety-copy">UX-00 原型：按钮仅演示确认步骤，不会真实发布。</p></section>
        <aside class="delivery-summary"><p class="kicker">将要交付</p><h2>为什么你每天都在用 AI，却没有因此早点下班？</h2><div class="summary-cover"></div><dl><div><dt>内容</dt><dd>主文章 · 约 1,200 字</dd></div><div><dt>配图</dt><dd>1 张封面 + 2 张插图</dd></div><div><dt>平台</dt><dd>{{ account }}</dd></div></dl><button class="markdown-link" type="button"><FileTextOutlined /> 同时导出 Markdown</button></aside>
      </div>
    </section>
  </main>
</template>

<style scoped>
.creator-prototype { min-height: 100vh; background: #f5f2eb; color: #25231f; }.topbar { height: 66px; display: flex; align-items: center; justify-content: space-between; padding: 0 40px; border-bottom: 1px solid #ded8cb; background: #fbfaf6; }.wordmark, .quiet-link, .text-button, .rail-back, .rail-action, .add-reference, .inline-comment button, .reference-item button, .markdown-link { border: 0; background: transparent; cursor: pointer; font: inherit; }.wordmark { color: #25231f; font-family: Georgia, 'Songti SC', serif; font-size: 20px; font-weight: 700; }.wordmark span { color: #a14f32; font-family: inherit; font-size: 12px; margin-left: 5px; }.quiet-link { color: #665e54; font-size: 14px; }.quiet-link span { color: #938a7f; margin-left: 5px; }.article-status { color: #686158; font-size: 13px; }.status-light { display: inline-block; width: 7px; height: 7px; margin-right: 5px; border-radius: 50%; background: #4f806d; }.kicker { margin: 0 0 10px; color: #9f4d31; font-size: 12px; font-weight: 700; letter-spacing: .06em; }.start-view { max-width: 830px; margin: 0 auto; padding: 76px 24px 56px; }.start-copy { max-width: 670px; }.start-copy h1, .loading-card h1, .diff-header h1, .final-header h1, .delivery-header h1 { margin: 0; font-family: Georgia, 'Songti SC', serif; font-size: clamp(36px, 5vw, 62px); line-height: 1.08; letter-spacing: -.035em; }.start-copy > p:last-child { max-width: 570px; color: #655f57; font-size: 18px; line-height: 1.65; }.composer { margin-top: 42px; padding: 28px; border: 1px solid #dcd4c7; border-radius: 12px; background: #fffdf8; box-shadow: 0 16px 40px rgba(69, 58, 42, .07); }.composer label, .delivery-card label { display: block; margin: 18px 0 7px; color: #3e3a34; font-size: 14px; font-weight: 700; }.composer label:first-child { margin-top: 0; }.reference-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 24px; }.field-label { display: block; color: #3e3a34; font-weight: 700; }.field-help { color: #81796f; font-size: 13px; }.add-reference { color: #70412e; font-weight: 700; white-space: nowrap; }.references { display: grid; gap: 8px; margin-top: 14px; }.reference-item { display: flex; align-items: center; gap: 8px; min-height: 38px; padding: 0 10px; border-radius: 7px; background: #f1eee7; color: #514b43; font-size: 13px; }.reference-item small { margin-left: auto; color: #547b69; }.reference-item.failed { background: #fff4ef; }.reference-item.failed small { color: #b05638; }.reference-item button { color: #7f756b; font-size: 20px; }.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 26px; color: #81796f; font-size: 13px; }.draft-note { color: #847c71; font-size: 13px; text-align: center; }.loading-view { display: grid; min-height: calc(100vh - 66px); place-items: center; padding: 24px; }.loading-card { width: min(100%, 560px); padding: 44px; text-align: center; }.paper-orbit { display: grid; width: 68px; height: 68px; margin: 0 auto 25px; place-items: center; border-radius: 50%; background: #e9d8cb; color: #9f4d31; font-size: 26px; animation: pulse 1.4s ease-in-out infinite; }.generation-steps { margin: 32px 0; text-align: left; }.generation-steps p { color: #a59b8e; }.generation-steps .done { color: #466d5d; }.generation-steps .anticon { margin-right: 8px; } @keyframes pulse { 50% { transform: scale(1.08); } }.article-view { display: grid; grid-template-columns: 210px minmax(0, 720px) 280px; max-width: 1320px; margin: 0 auto; }.article-rail, .comment-panel { padding: 32px 20px; }.article-rail { border-right: 1px solid #e1dbcf; }.rail-back { color: #6d655d; font-size: 13px; }.rail-section { display: grid; gap: 5px; margin: 38px 0 18px; }.rail-section strong { font-family: Georgia, serif; }.rail-section span, .panel-note { color: #8c8378; font-size: 12px; }.rail-action { display: block; width: 100%; padding: 10px 0; color: #514a42; text-align: left; font-size: 13px; }.rail-action .anticon { margin-right: 8px; color: #a14f32; }.article-paper, .final-paper { background: #fffdf8; }.article-paper { min-height: calc(100vh - 66px); padding: 68px clamp(30px, 7vw, 82px); box-shadow: 0 0 36px rgba(75, 65, 51, .06); }.article-meta { color: #9b7969; font-size: 12px; font-weight: 700; }.article-paper h1, .final-paper h1 { margin: 14px 0; font-family: Georgia, 'Songti SC', serif; font-size: clamp(36px, 4.2vw, 56px); line-height: 1.12; letter-spacing: -.04em; }.dek { color: #686057; font-family: Georgia, serif; font-size: 19px; line-height: 1.6; }.article-paper p:not(.dek), .final-paper p:not(.dek) { font-family: Georgia, 'Songti SC', serif; font-size: 18px; line-height: 1.9; }.cover-art { position: relative; height: 270px; margin: 36px 0; overflow: hidden; border-radius: 4px; background: linear-gradient(135deg, #e1c1a2, #56706a); }.cover-sun { position: absolute; top: 32px; right: 72px; width: 92px; height: 92px; border-radius: 50%; background: #f9e5bc; }.cover-desk { position: absolute; right: -20px; bottom: -20px; left: 42px; height: 110px; transform: skewY(-6deg); background: #513b2f; }.cover-art figcaption, .inline-art figcaption { position: absolute; right: 14px; bottom: 10px; left: 14px; color: #fffaf1; font-size: 12px; }.selectable { cursor: text; }.selectable.selected { margin-inline: -18px; padding: 0 18px; border-left: 3px solid #a14f32; background: #fceee6; }.inline-comment { margin: 15px -18px 22px; padding: 14px; border: 1px solid #d8c0b0; border-radius: 8px; background: #fffaf5; }.inline-comment > span { display: block; margin-bottom: 8px; color: #7d4934; font-size: 12px; font-weight: 700; }.inline-comment > div { display: flex; justify-content: flex-end; gap: 9px; margin-top: 8px; }.inline-comment button { color: #6c655d; }.inline-art { position: relative; height: 210px; margin: 34px 0; overflow: hidden; border-radius: 4px; background: #d9e2d3; }.path-line { position: absolute; top: 105px; left: 0; width: 100%; height: 4px; transform: rotate(-7deg); background: #7b6954; }.path-person { position: absolute; top: 65px; left: 44%; display: grid; width: 54px; height: 54px; place-items: center; border-radius: 50%; background: #a14f32; color: white; font-size: 13px; }.saved-note { padding: 12px; border-radius: 7px; background: #e9f0e9; color: #416a57; font-size: 13px; }.comment-panel { border-left: 1px solid #e1dbcf; }.comment-panel h2 { margin: 0 0 8px; font-family: Georgia, serif; font-size: 24px; }.comment-panel > p:not(.kicker) { color: #756d63; font-size: 13px; line-height: 1.6; }.comment-panel :deep(.ant-btn) { margin-top: 12px; }.diff-view, .final-view, .delivery-view { max-width: 1160px; margin: 0 auto; padding: 38px 32px 68px; }.diff-header, .final-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 28px; margin-bottom: 34px; }.diff-header h1, .final-header h1, .delivery-header h1 { font-size: clamp(31px, 4vw, 48px); }.scope-badge { padding: 6px 10px; border-radius: 99px; background: #e9eee7; color: #496756; font-size: 12px; }.diff-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }.diff-paper { min-height: 385px; padding: 35px; border: 1px solid #ddd5c9; border-radius: 8px; background: #fffdf8; font-family: Georgia, serif; font-size: 17px; line-height: 1.8; }.diff-paper h2 { font-size: 28px; line-height: 1.22; }.diff-label { margin-bottom: 20px; color: #7d756c; font-family: -apple-system, sans-serif; font-size: 12px; font-weight: 700; }.removed { padding: 3px 6px; background: #f8ddda; text-decoration: line-through; }.added { padding: 3px 6px; background: #dceee1; }.diff-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 18px; padding: 18px 0; }.diff-footer > p { max-width: 570px; color: #675f55; font-size: 13px; }.diff-footer > div { display: flex; gap: 10px; }.text-button { color: #6e655c; padding: 9px 12px; }.final-header > div:last-child { display: flex; align-items: center; gap: 10px; }.final-paper { max-width: 680px; margin: 0 auto; padding: 52px 70px; box-shadow: 0 8px 28px rgba(82, 70, 52, .08); }.final-cover { height: 290px; margin: 30px 0; border-radius: 4px; background: radial-gradient(circle at 76% 28%, #fae7bb 0 12%, transparent 13%), linear-gradient(135deg, #dfc2a8, #557169); }.markdown-link { display: block; margin: 22px auto 0; color: #77523f; font-weight: 700; }.delivery-header { max-width: 660px; }.delivery-header > p:last-child { color: #70675d; font-size: 16px; line-height: 1.65; }.delivery-layout { display: grid; grid-template-columns: minmax(0, 620px) 330px; gap: 24px; align-items: start; }.delivery-card, .delivery-summary { padding: 28px; border: 1px solid #ddd5c9; border-radius: 10px; background: #fffdf8; }.delivery-card label:first-child { margin-top: 0; }.channel-note { margin: 10px 0 20px; padding: 10px; border-radius: 6px; background: #eef1ea; color: #526450; font-size: 13px; }.delivery-radios { display: grid; gap: 12px; }.schedule-box { margin: 18px 0; padding: 15px; border-radius: 7px; background: #f3f0e8; }.schedule-box label { margin-top: 0; }.schedule-box p, .safety-copy { color: #80776d; font-size: 12px; line-height: 1.6; }.safety-copy { text-align: center; }.delivery-summary h2 { margin: 0 0 18px; font-family: Georgia, serif; font-size: 25px; line-height: 1.28; }.summary-cover { height: 145px; margin-bottom: 18px; border-radius: 5px; background: radial-gradient(circle at 72% 24%, #f9e6ba 0 13%, transparent 14%), linear-gradient(135deg, #ddc0a4, #567068); }.delivery-summary dl { display: grid; gap: 12px; }.delivery-summary dl div { display: flex; justify-content: space-between; gap: 12px; }.delivery-summary dt { color: #82796f; font-size: 12px; }.delivery-summary dd { margin: 0; color: #4d473f; font-size: 13px; text-align: right; }
.selection-hint { margin: 30px 0 6px !important; color: #897e73; font-family: -apple-system, BlinkMacSystemFont, sans-serif !important; font-size: 12px !important; line-height: 1.5 !important; }
.inline-comment blockquote { margin: 0 0 10px; padding: 8px 10px; border-left: 2px solid #d1a089; color: #6d5548; font-size: 13px; line-height: 1.55; }
.selection-menu { position: fixed; z-index: 12; transform: translate(8px, 8px); display: inline-flex; align-items: center; gap: 6px; padding: 8px 11px; border: 1px solid #4a372d; border-radius: 7px; background: #2e2723; box-shadow: 0 8px 20px rgba(45, 33, 25, .24); color: #fffdf8; cursor: pointer; font-size: 13px; font-weight: 700; }.selection-menu:active { transform: translate(8px, 9px); }.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (max-width: 1100px) { .article-view { grid-template-columns: 165px minmax(0, 1fr); }.comment-panel { grid-column: 2; border-top: 1px solid #e1dbcf; border-left: 0; }.delivery-layout { grid-template-columns: 1fr 300px; } }
@media (max-width: 720px) { .topbar { padding: 0 17px; }.article-status { display: none; }.quiet-link span { display: none; }.start-view { padding: 48px 17px; }.composer { padding: 19px; }.reference-row, .composer-footer, .diff-header, .final-header, .diff-footer { align-items: flex-start; flex-direction: column; }.article-view { display: block; }.article-rail { display: flex; gap: 5px; overflow-x: auto; padding: 15px; border-right: 0; border-bottom: 1px solid #e1dbcf; }.rail-section { display: none; }.rail-action, .rail-back { width: auto; min-width: max-content; padding: 8px; }.article-paper { min-height: auto; padding: 42px 24px; }.comment-panel { padding: 24px; }.diff-view, .final-view, .delivery-view { padding: 26px 17px 48px; }.diff-grid, .delivery-layout { grid-template-columns: 1fr; }.diff-paper { min-height: auto; padding: 24px; }.final-paper { padding: 35px 24px; }.final-header > div:last-child { align-items: flex-start; flex-direction: column; } }
</style>
