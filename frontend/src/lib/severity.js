export const SEVERITY_COLORS = {
  critical: '#EF4444',
  high: '#F97316',
  medium: '#EAB308',
  low: '#6366F1',
}

export function severityKey(value) {
  return typeof value === 'string' && value.trim() ? value.trim().toLowerCase() : 'unknown'
}

export function severityLabel(value) {
  return severityKey(value).toUpperCase()
}

export function severityColor(value, colors = SEVERITY_COLORS) {
  return colors[severityKey(value)] || 'var(--text-2)'
}
