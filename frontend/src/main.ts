// M10-7 SPA 入口
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import { router } from './router'
import { installChunkRecovery } from './router/chunkRecovery'
import './style.css'

const app = createApp(App)
app.use(createPinia())
installChunkRecovery(router)
app.use(router)
app.use(Antd)
app.mount('#app')
