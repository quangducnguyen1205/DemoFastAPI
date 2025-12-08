import { Link } from 'react-router-dom'

interface VideoItemProps {
  id: number
  title: string
  status: string
}

function VideoItem({ id, title, status }: VideoItemProps) {
  const statusConfig = {
    processing: {
      badge: 'bg-yellow-100 text-yellow-800',
      label: 'Processing',
      icon: (
        <svg className="animate-spin h-4 w-4 text-yellow-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      ),
    },
    ready: {
      badge: 'bg-green-100 text-green-800',
      label: 'Ready',
      icon: (
        <svg className="h-4 w-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7"></path>
        </svg>
      ),
    },
    failed: {
      badge: 'bg-red-100 text-red-800',
      label: 'Failed',
      icon: (
        <svg className="h-4 w-4 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path>
        </svg>
      ),
    },
  }

  // Normalize status: use 'processing' as fallback for unknown/null values
  const normalizedStatus = (status && status in statusConfig) ? status as keyof typeof statusConfig : 'processing'
  const config = statusConfig[normalizedStatus]

  return (
    <Link to={`/videos/${id}`} className="block">
      <div className="flex items-center p-3 bg-gray-50 rounded-md hover:bg-gray-100 transition-colors">
        <div className="w-12 h-12 bg-gray-200 rounded mr-3 flex items-center justify-center">
          <span className="text-gray-400 text-lg">📹</span>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-700 truncate">{title}</p>
          <div className="flex items-center mt-1">
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${config.badge}`}>
              <span className="mr-1">{config.icon}</span>
              {config.label}
            </span>
          </div>
        </div>
      </div>
    </Link>
  )
}

export default VideoItem
