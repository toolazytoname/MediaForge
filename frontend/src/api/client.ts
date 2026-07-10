// M10-7 axios 客户端：baseURL = /api/v1，统一错误解包

import axios, { type AxiosError, type AxiosResponse } from 'axios'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v1',
  timeout: 30_000,
})

// M10 P2 阶段 C：写端点（POST）共用 helper
// 直接暴露 axios.post 不需要 wrapper——但为类型清晰提供一个泛型版
export async function apiPost<T = unknown>(
  url: string,
  body?: unknown,
): Promise<AxiosResponse<T>> {
  return api.post<T>(url, body)
}

export interface ApiError {
  code: string
  message: string
}

export function unwrapError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<{ detail?: { error?: ApiError }; error?: ApiError }>
    // FastAPI HTTPException 把 detail 包装一层，response 形如
    //   {"detail": {"error": {"code": "...", "message": "..."}}}
    // 直发的 JSONResponse（如 201 创建成功之外的某些端点）则是
    //   {"error": {"code": "...", "message": "..."}}
    const detail = ax.response?.data?.detail?.error ?? ax.response?.data?.error
    if (detail) return `${detail.code}: ${detail.message}`
    if (ax.message) return ax.message
  }
  if (err instanceof Error) return err.message
  return String(err)
}
