import { useEffect, useRef } from 'react'
import axiosClient from '../api/axiosClient'

interface PendingUpload {
  taskId: string
  videoId: number
  title: string
}

interface VideoProcessingTrackerProps {
  pending: PendingUpload[]
  onVideoReady: (videoId: number) => void
  onVideoFailed: (videoId: number) => void
  onRemovePending: (taskId: string) => void
}

function VideoProcessingTracker({
  pending,
  onVideoReady,
  onVideoFailed,
  onRemovePending,
}: VideoProcessingTrackerProps) {
  const intervalRef = useRef<number | null>(null)

  useEffect(() => {
    if (pending.length === 0) {
      // No pending tasks, clear interval
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      return
    }

    const pollTasks = async () => {
      for (const upload of pending) {
        try {
          const response = await axiosClient.get(`/videos/tasks/${upload.taskId}`)
          const { status } = response.data

          if (status === 'SUCCESS') {
            onVideoReady(upload.videoId)
            onRemovePending(upload.taskId)
          } else if (status === 'FAILURE') {
            onVideoFailed(upload.videoId)
            onRemovePending(upload.taskId)
          }
          // If PENDING or STARTED, continue polling
        } catch (err) {
          console.error(`Failed to poll task ${upload.taskId}:`, err)
        }
      }
    }

    // Poll immediately on mount/change
    pollTasks()

    // Set up interval polling every 3 seconds
    intervalRef.current = window.setInterval(pollTasks, 3000)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [pending, onVideoReady, onVideoFailed, onRemovePending])

  if (pending.length === 0) {
    return null
  }

  return (
    <div className="space-y-2">
      {pending.map((upload) => (
        <div
          key={upload.taskId}
          className="flex items-center p-2 bg-yellow-50 border border-yellow-200 rounded-md"
        >
          <svg
            className="animate-spin h-4 w-4 text-yellow-600 mr-2"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            ></circle>
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            ></path>
          </svg>
          <span className="text-sm text-yellow-800 truncate">
            Processing: {upload.title}
          </span>
        </div>
      ))}
    </div>
  )
}

export default VideoProcessingTracker
