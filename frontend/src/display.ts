const deliveries: Record<string, string> = {
  SENT: '已发送', FAILED: '发送失败', UNKNOWN: '回执待核对', SENDING: '发送中',
  SKIPPED: '已跳过', PENDING: '待发送', NOT_GENERATED: '尚未生成',
  DISABLED: '已关闭', NOT_DUE: '未到时间', NONE: '尚无记录',
}
export function deliveryText(status?: string | null) {
  if (!status) return '尚无记录'
  return deliveries[status] || (/[\u3400-\u9fff]/.test(status) ? status : '回执待核对')
}

const reasons: Record<string, string> = {
  AUTH: '行情授权需要检查', HTTP_ERROR: '行情服务请求失败', API_ERROR: '行情服务返回错误',
  NETWORK_UNAVAILABLE: '暂时连不上行情服务', RATE_LIMIT: '行情服务请求过于频繁',
  REQUEST_BUDGET: '今日可用行情请求已达上限或已预留', SOURCE_PAUSED: '行情服务暂时停用',
  UPSTREAM_ERROR: '行情源暂时无法提供数据', SCHEMA_CHANGED: '行情源返回格式有变',
  EMPTY: '行情源未返回数据', STALE: '行情尚未更新', CALENDAR: '交易日历需要更新',
  INVALID_BARS: '日线数据未通过校验', INSUFFICIENT_DATA: '可用历史日线不足',
  DEPENDENCY_MISSING: '行情组件待配置', SYMBOL_MISSING: '未取得该股行情',
  CONNECT_FAILED: '发送连接失败', NO_RECEIPT: '尚未取得投递回执',
}
export function reasonText(value?: string | null): string {
  return (value || '').split('\n').map((line) => {
    const match = line.match(/^(?:(\d{6}\.(?:SH|SZ))\s+)?([A-Z][A-Z_]+)\b/)
    if (match && match[2] in reasons) return [match[1], reasons[match[2]]].filter(Boolean).join(' ')
    return line.replace(/\bOHLC\b/g, '开盘价、最高价、最低价和收盘价')
  }).join('\n')
}
