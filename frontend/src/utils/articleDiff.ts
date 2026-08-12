export type DiffRow = { kind: 'same' | 'add' | 'remove'; text: string; line: number }

// A small LCS diff deliberately works on Markdown lines: paragraphs, list
// items, headings and image links stay recognisable instead of becoming an
// opaque before/after blob.  It never renders HTML from either document.
export function lineDiff(before: string, after: string): DiffRow[] {
  const left = before.split('\n'); const right = after.split('\n')
  const matrix = Array.from({ length: left.length + 1 }, () => Array<number>(right.length + 1).fill(0))
  for (let i = left.length - 1; i >= 0; i--) for (let j = right.length - 1; j >= 0; j--) {
    matrix[i][j] = left[i] === right[j] ? matrix[i + 1][j + 1] + 1 : Math.max(matrix[i + 1][j], matrix[i][j + 1])
  }
  const rows: DiffRow[] = []; let i = 0; let j = 0; let line = 1
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) { rows.push({ kind: 'same', text: left[i++], line: line++ }); j++; continue }
    if (j < right.length && (i === left.length || matrix[i][j + 1] >= matrix[i + 1][j])) { rows.push({ kind: 'add', text: right[j++], line }); continue }
    rows.push({ kind: 'remove', text: left[i++], line: line++ })
  }
  return rows
}
