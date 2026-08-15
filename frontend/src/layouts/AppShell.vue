<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UserAvatarMenu from './components/UserAvatarMenu.vue'

interface NavItem {
  path: string
  label: string
  exact?: boolean
}

const primaryItems: ReadonlyArray<NavItem> = [
  { path: '/', label: '写作', exact: true },
  { path: '/projects', label: '文章' },
  { path: '/settings', label: '设置' },
]

const moreItems: ReadonlyArray<NavItem> = [
  { path: '/ideas', label: '灵感' },
]

const route = useRoute()
const router = useRouter()
const moreOpen = ref(false)
const mobileOpen = ref(false)

const moreActive = computed(() => moreItems.some(item => isActive(item)))

function isActive(item: NavItem): boolean {
  if (item.exact) return route.path === item.path
  return route.path === item.path || route.path.startsWith(`${item.path}/`)
}

function go(path: string): void {
  moreOpen.value = false
  mobileOpen.value = false
  if (route.fullPath !== path) void router.push(path)
}

function onMoreBlur(event: FocusEvent): void {
  const next = event.relatedTarget as Node | null
  const root = event.currentTarget as HTMLElement
  if (!next || !root.contains(next)) moreOpen.value = false
}
</script>

<template>
  <div class="shell">
    <header class="topbar">
      <a class="brand" href="/" @click.prevent="go('/')">
        <span class="mark" aria-hidden="true"></span>
        MediaForge
      </a>
      <nav class="primary" aria-label="主导航">
        <button
          v-for="item in primaryItems"
          :key="item.path"
          type="button"
          :class="['nav-link', { active: isActive(item) }]"
          :aria-current="isActive(item) ? 'page' : undefined"
          @click="go(item.path)"
        >
          {{ item.label }}
        </button>
        <div class="more" @focusout="onMoreBlur">
          <button
            type="button"
            :class="['nav-link', { active: moreActive || moreOpen }]"
            :aria-expanded="moreOpen"
            aria-haspopup="true"
            @click="moreOpen = !moreOpen"
          >
            更多
          </button>
          <div v-if="moreOpen" class="menu" role="menu">
            <button
              v-for="item in moreItems"
              :key="item.path"
              type="button"
              role="menuitem"
              :class="{ active: isActive(item) }"
              @click="go(item.path)"
            >
              {{ item.label }}
            </button>
          </div>
        </div>
      </nav>
      <div class="end">
        <UserAvatarMenu />
        <button
          type="button"
          class="burger"
          :aria-expanded="mobileOpen"
          aria-label="打开菜单"
          @click="mobileOpen = !mobileOpen"
        >
          <span :class="{ open: mobileOpen }"></span>
        </button>
      </div>
    </header>

    <div v-if="mobileOpen" class="sheet" role="dialog" aria-label="站点菜单">
      <button
        v-for="item in primaryItems"
        :key="item.path"
        type="button"
        :class="{ active: isActive(item) }"
        @click="go(item.path)"
      >
        {{ item.label }}
      </button>
      <button
        v-for="item in moreItems"
        :key="item.path"
        type="button"
        :class="{ active: isActive(item) }"
        @click="go(item.path)"
      >
        {{ item.label }}
      </button>
    </div>

    <main id="main" class="stage">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100dvh;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: var(--z-nav);
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  height: var(--nav-h);
  padding: 0 28px;
  border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--canvas) 88%, transparent);
  backdrop-filter: saturate(1.2) blur(16px);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 560;
  letter-spacing: -0.02em;
  text-decoration: none;
}

.mark {
  width: 14px;
  height: 14px;
  border: 1.5px solid var(--ink);
  border-radius: 2px 8px 2px 8px;
}

.primary {
  display: flex;
  justify-content: center;
  gap: 4px;
}

.nav-link {
  height: 32px;
  padding: 0 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.nav-link:hover,
.nav-link.active {
  color: var(--ink);
  background: var(--wash);
}

.end {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-self: end;
}

.more {
  position: relative;
}

.menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 50%;
  min-width: 140px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--surface);
  transform: translateX(-50%);
}

.menu button,
.sheet button {
  display: block;
  width: 100%;
  padding: 8px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.menu button:hover,
.menu button.active,
.sheet button:hover,
.sheet button.active {
  background: var(--wash);
}

.burger {
  display: none;
  width: 36px;
  height: 36px;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.burger span,
.burger span::before,
.burger span::after {
  display: block;
  width: 16px;
  height: 1.5px;
  margin: 0 auto;
  background: var(--ink);
  transition: transform 200ms var(--ease), opacity 200ms var(--ease);
}

.burger span::before,
.burger span::after {
  content: "";
}

.burger span::before {
  transform: translateY(-5px);
}

.burger span::after {
  transform: translateY(3.5px);
}

.burger span.open {
  background: transparent;
}

.burger span.open::before {
  transform: translateY(0) rotate(45deg);
}

.burger span.open::after {
  transform: translateY(-1.5px) rotate(-45deg);
}

.sheet {
  display: none;
  padding: 8px 16px 20px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}

.stage {
  width: min(1120px, calc(100% - 48px));
  margin: 0 auto;
  padding: 28px 0 80px;
}

@media (max-width: 768px) {
  .topbar {
    padding: 0 16px;
  }

  .primary {
    display: none;
  }

  .burger,
  .sheet {
    display: block;
  }

  .stage {
    width: min(1120px, calc(100% - 32px));
    padding-top: 20px;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .topbar {
    background: var(--canvas);
    backdrop-filter: none;
  }
}
</style>
