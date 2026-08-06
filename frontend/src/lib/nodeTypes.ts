import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { NodeTypeInfo } from '../types'

export function useNodeTypes() {
  return useQuery({
    queryKey: ['node-types'],
    queryFn: () => api.get<NodeTypeInfo[]>('/node-types'),
    staleTime: Infinity, // the node type registry is static for the lifetime of a session
  })
}
