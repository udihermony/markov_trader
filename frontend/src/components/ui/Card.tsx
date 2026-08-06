import type { HTMLAttributes } from 'react'

export function Card({ className = '', ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={`bg-white rounded-lg border border-slate-200 shadow-sm p-4 ${className}`}
      {...props}
    />
  )
}
