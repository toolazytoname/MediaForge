import type { Router } from 'vue-router'

const RECOVERY_KEY = 'mediaforge:chunk-recovery-attempted'
const CHUNK_FAILURE = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i

/** Recover once after a deployment replaces Vite's hashed route chunks. */
export function installChunkRecovery(router: Router): void {
  const recover = (error: Error) => {
    if (!CHUNK_FAILURE.test(error.message)) return

    if (!window.sessionStorage.getItem(RECOVERY_KEY)) {
      window.sessionStorage.setItem(RECOVERY_KEY, '1')
      window.location.reload()
      return
    }

    window.dispatchEvent(new CustomEvent('mediaforge:chunk-update-required'))
  }

  router.onError(recover)
  window.addEventListener('vite:preloadError', (event) => {
    event.preventDefault()
    recover((event as CustomEvent<Error>).detail)
  })

  router.afterEach(() => window.sessionStorage.removeItem(RECOVERY_KEY))
}
