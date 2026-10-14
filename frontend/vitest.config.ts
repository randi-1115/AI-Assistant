import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // SSE 解析是纯逻辑，不需要 DOM。
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
