import { useEffect, useRef } from 'react'
import axiosClient from '../api/axiosClient'
import { Spinner } from './ui'

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
    <div className="space-y-2 mb-3">
      {pending.map((upload) => (
        <div
          key={upload.taskId}
          className="flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-xl"
        >
          <Spinner size="sm" className="text-amber-600" />
          <span className="text-sm text-amber-800 truncate flex-1">
            Processing: <span className="font-medium">{upload.title}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

export default VideoProcessingTracker
