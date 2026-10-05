const identifier =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi
const digest = /\b[0-9a-f]{64}\b/gi

export function humanize(value: string) {
  const text = displayText(value).replaceAll('_', ' ').replaceAll('.', ' ')
  return text ? text[0].toUpperCase() + text.slice(1) : 'Not recorded'
}

export function displayText(value: unknown): string {
  if (value == null || value === '') return 'Not recorded'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'string' || typeof value === 'number') {
    return String(value)
      .replace(identifier, 'linked record')
      .replace(digest, 'recorded checksum')
  }
  if (Array.isArray(value)) return value.map(displayText).join(', ')
  if (typeof value === 'object') {
    return (
      Object.entries(value)
        .filter(([key]) => !/(^id$|_ids?$|hash|signature|token)/i.test(key))
        .map(([key, item]) => `${humanize(key)}: ${displayText(item)}`)
        .join('; ') || 'Linked evidence'
    )
  }
  return 'Not recorded'
}

export function recordLabel(kind: string, name?: string | null) {
  return name && !name.match(identifier) ? displayText(name) : humanize(kind)
}
