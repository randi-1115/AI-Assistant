import { describe, expect, it } from 'vitest'

import {
  attachmentsFromMetadata,
  sourcesFromMetadata,
  toolStatusesFromMetadata,
} from './source'

describe('sourcesFromMetadata', () => {
  it('解析文件来源', () => {
    const sources = sourcesFromMetadata({
      sources: [
        {
          kind: 'file',
          file_id: 12,
          chunk_index: 3,
          title: '报告.pdf',
          url: null,
          section_title: '第 2 页',
          snippet: '关键结论',
          score: 0.87,
        },
      ],
    })
    expect(sources).toEqual([
      {
        kind: 'file',
        file_id: 12,
        chunk_index: 3,
        title: '报告.pdf',
        url: null,
        section_title: '第 2 页',
        snippet: '关键结论',
        score: 0.87,
        excerpt_number: null,
        citation_label: null,
      },
    ])
  })

  it('解析 web 来源', () => {
    expect(
      sourcesFromMetadata({
        sources: [{ kind: 'web', title: '示例', url: 'https://example.com', snippet: '摘要' }],
      }),
    ).toEqual([
      {
        kind: 'web',
        title: '示例',
        url: 'https://example.com',
        snippet: '摘要',
        published_date: null,
        domain: null,
        score: null,
      },
    ])
  })

  it('解析网页发布时间', () => {
    expect(
      sourcesFromMetadata({
        sources: [
          {
            kind: 'web',
            title: '新闻',
            url: 'https://example.com/news',
            snippet: '摘要',
            published_date: '2026-11-12',
          },
        ],
      }),
    ).toEqual([
      {
        kind: 'web',
        title: '新闻',
        url: 'https://example.com/news',
        snippet: '摘要',
        published_date: '2026-11-12',
        domain: null,
        score: null,
      },
    ])
  })

  it('兼容没有 kind 的旧格式', () => {
    expect(
      sourcesFromMetadata({ sources: [{ title: '旧来源', url: 'https://old.example' }] }),
    ).toEqual([
      {
        kind: 'web',
        title: '旧来源',
        url: 'https://old.example',
        snippet: null,
        published_date: null,
        domain: null,
        score: null,
      },
    ])
  })

  it('丢弃坏数据而不是抛错', () => {
    expect(
      sourcesFromMetadata({
        sources: [
          null,
          'bad',
          { kind: 'file', title: '缺字段' },
          { kind: 'file', file_id: '1', chunk_index: 0, title: 'x', score: 1 },
        ],
      }),
    ).toEqual([])
  })

  it('metadata 为空时返回空数组', () => {
    expect(sourcesFromMetadata(null)).toEqual([])
    expect(sourcesFromMetadata({})).toEqual([])
  })

  it('丢弃 javascript:/data: 链接', () => {
    expect(
      sourcesFromMetadata({
        sources: [
          { kind: 'web', title: 'bad', url: 'javascript:alert(1)' },
          { kind: 'web', title: 'bad2', url: 'data:text/html,x' },
        ],
      }),
    ).toEqual([])
  })
})

describe('toolStatusesFromMetadata', () => {
  it('解析终态与 provider', () => {
    expect(
      toolStatusesFromMetadata({
        tool_statuses: [
          {
            tool: 'web_search',
            status: 'done',
            message: '已找到 3 条结果',
            provider: 'tavily',
          },
          { tool: 'web_search', status: 'unavailable', message: '鉴权失败', provider: null },
        ],
      }),
    ).toEqual([
      {
        tool: 'web_search',
        status: 'done',
        message: '已找到 3 条结果',
        provider: 'tavily',
      },
      {
        tool: 'web_search',
        status: 'unavailable',
        message: '鉴权失败',
        provider: null,
      },
    ])
  })

  it('解析 failed 与 P2 新字段', () => {
    expect(
      toolStatusesFromMetadata({
        tool_statuses: [
          {
            tool: 'calculator',
            status: 'failed',
            message: '工具执行失败：除数不能为 0',
            call_id: 'call_1',
            arguments_summary: 'expression="1/0"',
            duration_ms: 3,
            error_code: 'invalid_expression',
          },
        ],
      }),
    ).toEqual([
      {
        tool: 'calculator',
        status: 'failed',
        message: '工具执行失败：除数不能为 0',
        provider: null,
        call_id: 'call_1',
        arguments_summary: 'expression="1/0"',
        duration_ms: 3,
        error_code: 'invalid_expression',
      },
    ])
  })

  it('丢弃未知状态和坏数据', () => {
    expect(
      toolStatusesFromMetadata({
        tool_statuses: [
          null,
          'bad',
          { tool: 'web_search', status: 'weird' },
          { tool: 123, status: 'done' },
        ],
      }),
    ).toEqual([])
  })

  it('metadata 为空时返回空数组', () => {
    expect(toolStatusesFromMetadata(null)).toEqual([])
    expect(toolStatusesFromMetadata({})).toEqual([])
  })
})

describe('attachmentsFromMetadata', () => {
  it('解析附件快照', () => {
    expect(
      attachmentsFromMetadata({
        attachments: [
          {
            id: 7,
            name: '需求.txt',
            parse_status: 'ready',
            supported_for_qa: true,
          },
        ],
      }),
    ).toEqual([
      {
        id: 7,
        name: '需求.txt',
        parse_status: 'ready',
        supported_for_qa: true,
        error_message: undefined,
      },
    ])
  })

  it('未知状态被丢弃', () => {
    expect(
      attachmentsFromMetadata({
        attachments: [{ id: 7, name: 'x', parse_status: 'weird', supported_for_qa: true }],
      }),
    ).toEqual([])
  })
})
