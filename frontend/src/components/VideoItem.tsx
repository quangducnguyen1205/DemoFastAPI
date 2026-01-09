import { Link } from 'react-router-dom'
import { Badge, Spinner } from './ui'

interface VideoItemProps {
  id: number
  title: string
  status: string
}

function VideoItem({ id, title, status }: VideoItemProps) {
  const statusConfig = {
    processing: {
      variant: 'warning' as const,
      label: 'Processing',
      showSpinner: true,
    },
    ready: {
      variant: 'success' as const,
      label: 'Ready',
      showSpinner: false,
    },
    failed: {
      variant: 'error' as const,
      label: 'Failed',
      showSpinner: false,
    },
  }

  // Normalize status: use 'processing' as fallback for unknown/null values
  const normalizedStatus = (status && status in statusConfig) ? status as keyof typeof statusConfig : 'processing'
  const config = statusConfig[normalizedStatus]

  return (
    <Link to={`/videos/${id}`} className="block group">
      <div className="flex items-center gap-3 p-3 rounded-xl border border-app-border bg-white hover:border-brand-200 hover:bg-brand-50/30 transition-all">
        {/* Video thumbnail placeholder */}
        <div className="w-12 h-12 bg-gray-100 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-brand-100 transition-colors">
          <svg className="w-5 h-5 text-gray-400 group-hover:text-brand-500 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </div>

        {/* Video info */}
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-900 truncate group-hover:text-brand-700 transition-colors">
            {title}
          </p>
          <div className="flex items-center gap-2 mt-1">
            <Badge variant={config.variant} size="sm">
              {config.showSpinner && (
                <Spinner size="xs" className="mr-1" />
              )}
              {config.label}
            </Badge>
          </div>
        </div>

        {/* Arrow indicator */}
        <svg className="w-5 h-5 text-gray-300 group-hover:text-brand-500 transition-colors flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
        </svg>
      </div>
    </Link>
  )
}

export default VideoItem
