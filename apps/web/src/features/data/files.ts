export const MAX_IMPORT_BYTES = 5 * 1024 * 1024
export const MAX_SOURCE_BYTES = 1_048_576

export async function readImportFile(file: File) {
  if (!file.size || file.size > MAX_IMPORT_BYTES)
    throw new Error('Import files must be non-empty and no larger than 5 MiB.')
  const extension = file.name.split('.').at(-1)?.toLowerCase()
  if (extension !== 'csv' && extension !== 'json')
    throw new Error('Choose a CSV or JSON file.')
  const content = new TextDecoder('utf-8', {
    fatal: true,
    ignoreBOM: true,
  }).decode(await file.arrayBuffer())
  if (extension === 'json') {
    let data: unknown
    try {
      data = JSON.parse(content)
    } catch {
      throw new Error('The selected file is not valid JSON.')
    }
    if (!data || typeof data !== 'object')
      throw new Error(
        'JSON imports require row objects, an array of rows, or a records/products collection.',
      )
    const envelope = data as Record<string, unknown>
    const rows: unknown = Array.isArray(data)
      ? data
      : (envelope.records ?? envelope.products ?? [data])
    if (
      !Array.isArray(rows) ||
      rows.some((row) => !row || typeof row !== 'object' || Array.isArray(row))
    )
      throw new Error('Every import row must be an object.')
    if (rows.length > 5_000)
      throw new Error('Imports are limited to 5,000 rows.')
  }
  // Submit the original text so JSON decimal values never pass through JS numbers.
  return {
    filename: file.name,
    content,
    content_type:
      extension === 'csv'
        ? ('text/csv' as const)
        : ('application/json' as const),
  }
}

export async function readSourceFile(file: File) {
  if (!file.size || file.size > MAX_SOURCE_BYTES)
    throw new Error(
      'Source documents must be non-empty and no larger than 1 MiB.',
    )
  const types: Record<string, string> = {
    txt: 'text/plain',
    md: 'text/markdown',
    csv: 'text/csv',
    json: 'application/json',
    pdf: 'application/pdf',
  }
  const content_type = types[file.name.split('.').at(-1)?.toLowerCase() ?? '']
  if (!content_type)
    throw new Error('Choose a TXT, Markdown, CSV, JSON or PDF document.')
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (content_type !== 'application/pdf') {
    try {
      new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    } catch {
      throw new Error('Text documents must use UTF-8 encoding.')
    }
  }
  let binary = ''
  for (let start = 0; start < bytes.length; start += 8192)
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192))
  return {
    filename: file.name,
    content_type,
    encoding: 'base64' as const,
    content: btoa(binary),
  }
}
