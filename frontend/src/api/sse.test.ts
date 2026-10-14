import { describe, expect, it } from 'vitest'

import { parseSSEBlock, SSEParser } from './sse'

describe('parseSSEBlock', () => {
  it('解析事件名与 JSON 载荷', () => {
    expect(parseSSEBlock('event: token\ndata: {"text":"你好"}')).toEqual({
      event: 'token',
      data: { text: '你好' },
    })
  })

  it('没有 event 字段时默认事件名为 message', () => {
    expect(parseSSEBlock('data: {"a":1}')).toEqual({ event: 'message', data: { a: 1 } })
  })

  it('忽略注释行（服务端保活心跳）', () => {
    expect(parseSSEBlock(': ping\nevent: token\ndata: {"text":"x"}')).toEqual({
      event: 'token',
      data: { text: 'x' },
    })
  })

  it('剥掉冒号后的一个前导空格', () => {
    expect(parseSSEBlock('event:token\ndata:  {"text":"x"}')).toEqual({
      event: 'token',
      data: { text: 'x' },
    })
  })

  it('多行 data 用换行拼接', () => {
    expect(parseSSEBlock('event: token\ndata: {"text":\ndata: "x"}')).toEqual({
      event: 'token',
      data: { text: 'x' },
    })
  })

  it('兼容 CRLF', () => {
    expect(parseSSEBlock('event: token\r\ndata: {"text":"x"}\r\n')).toEqual({
      event: 'token',
      data: { text: 'x' },
    })
  })

  it('没有 data 的块返回 null', () => {
    expect(parseSSEBlock('event: token')).toBeNull()
  })

  it('载荷不是合法 JSON 时返回 null 而不是抛错', () => {
    expect(parseSSEBlock('event: token\ndata: {坏掉的')).toBeNull()
  })
})

describe('SSEParser', () => {
  const frame = (event: string, text: string): string =>
    `event: ${event}\ndata: {"text":"${text}"}\n\n`

  it('一次 push 拿到多个完整事件', () => {
    const parser = new SSEParser()
    const events = parser.push(frame('token', 'a') + frame('token', 'b'))
    expect(events).toEqual([
      { event: 'token', data: { text: 'a' } },
      { event: 'token', data: { text: 'b' } },
    ])
  })

  it('一帧被 TCP 切成两个 chunk 时不会丢也不会重复', () => {
    const parser = new SSEParser()
    const whole = frame('token', '你好')

    // 从中间任意位置切开，结果都应该一致
    for (let split = 1; split < whole.length; split += 1) {
      const open = new SSEParser()
      const first = open.push(whole.slice(0, split))
      const second = open.push(whole.slice(split))
      expect([...first, ...second]).toEqual([{ event: 'token', data: { text: '你好' } }])
    }
  })

  it('一个 chunk 里包含半帧加一整帧', () => {
    const parser = new SSEParser()
    const whole = frame('token', 'a') + frame('token', 'b')
    const cut = frame('token', 'a').length - 3

    expect(parser.push(whole.slice(0, cut))).toEqual([])
    expect(parser.push(whole.slice(cut))).toEqual([
      { event: 'token', data: { text: 'a' } },
      { event: 'token', data: { text: 'b' } },
    ])
  })

  it('chunk 以 \\r 结尾、下一个以 \\n 开头时不会凭空产生事件边界', () => {
    const parser = new SSEParser()
    expect(parser.push('event: token\r')).toEqual([])
    expect(parser.push('\ndata: {"text":"x"}\r')).toEqual([])
    expect(parser.push('\n\r\n')).toEqual([{ event: 'token', data: { text: 'x' } }])
  })

  it('兼容 CRLF 分隔的事件', () => {
    const parser = new SSEParser()
    expect(parser.push('event: token\r\ndata: {"text":"x"}\r\n\r\n')).toEqual([
      { event: 'token', data: { text: 'x' } },
    ])
  })

  it('flush 处理最后一个没有空行收尾的事件', () => {
    const parser = new SSEParser()
    expect(parser.push('event: message_end\ndata: {"finish_reason":"stop"}')).toEqual([])
    expect(parser.flush()).toEqual([
      { event: 'message_end', data: { finish_reason: 'stop' } },
    ])
  })

  it('flush 在没有残留数据时返回空数组', () => {
    const parser = new SSEParser()
    parser.push(frame('token', 'a'))
    expect(parser.flush()).toEqual([])
  })

  it('中文被正确还原（多字节字符跨 chunk 由 TextDecoder 保证）', () => {
    const parser = new SSEParser()
    const events = parser.push('event: token\ndata: {"text":"中文测试"}\n\n')
    expect(events[0]?.data).toEqual({ text: '中文测试' })
  })
})
