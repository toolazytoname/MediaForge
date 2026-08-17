export type AutonomyKey = 'assist' | 'collaborate' | 'draft' | 'pack'

export interface AutonomyPolicyView {
  key: AutonomyKey
  label: string
  help: string
  llm_allowed: boolean
  image_gen_allowed: boolean
}

export const AUTONOMY_OPTIONS: ReadonlyArray<AutonomyPolicyView> = [
  { key: 'assist', label: '手工', help: '零 LLM：只手录、手导 PNG、手点导出或草稿。', llm_allowed: false, image_gen_allowed: false },
  { key: 'collaborate', label: '协作', help: '显式点击才生成建议或预览；接受前不落盘。', llm_allowed: true, image_gen_allowed: true },
  { key: 'draft', label: 'AI 起草', help: '可提出主稿建议和未锁定平台草稿。', llm_allowed: true, image_gen_allowed: true },
  { key: 'pack', label: '自动内容包', help: '可准备到待审批；不得草稿或直发。', llm_allowed: true, image_gen_allowed: true },
]

export function policyLabel(key: AutonomyKey | string | undefined): string {
  return AUTONOMY_OPTIONS.find((item) => item.key === key)?.label ?? '协作'
}

export function llmForbiddenHint(key: AutonomyKey | string | undefined): string {
  return `${policyLabel(key)}模式为零 LLM。如需 AI，请把自主程度改为协作或 AI 起草。`
}
