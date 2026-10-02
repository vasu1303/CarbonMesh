export function formatDecimal(value: string) {
  if (/[eE]/.test(value)) return value
  const [integer, fraction] = value.split('.')
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const trimmed = fraction?.replace(/0+$/, '')
  return trimmed ? `${grouped}.${trimmed}` : grouped
}

export function formatDate(value: string) {
  return (
    new Intl.DateTimeFormat('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
    }).format(new Date(value)) + ' UTC'
  )
}

export function humanize(value: string) {
  return value.replace(/[_.]/g, ' ')
}
