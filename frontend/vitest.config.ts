import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // SSE 解析是纯逻辑，不需要 DOM。
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // 默认的 forks/threads 池在当前 Vitest 5.0.4 + Node 22 环境下会让测试
    // 文件拿到另一份 vitest 模块实例，describe/it 找不到 runner 状态
    // （"找不到 current suite"）。vmForks 在独立 VM 上下文里跑，状态一致。
    pool: 'vmForks',
  },
})
