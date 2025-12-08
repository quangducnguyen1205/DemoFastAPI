import { useState, useEffect, useCallback } from 'react'
import VideoUploadCard from '../components/VideoUploadCard'
import VideoProcessingTracker from '../components/VideoProcessingTracker'
import VideoItem from '../components/VideoItem'
import SemanticSearchCard from '../components/SemanticSearchCard'
import axiosClient from '../api/axiosClient'
import { useAuth } from '../context/AuthContext'

interface Video {
  id: number
  title: string
  //status: 'processing' | 'ready' | 'failed'
  status: string
}

interface PendingUpload {
  taskId: string
  videoId: number
  title: string
}

function DashboardPage() {
  const [videos, setVideos] = useState<Video[]>([])
  const [pendingUploads, setPendingUploads] = useState<PendingUpload[]>([])
  const [loading, setLoading] = useState(true)
  const { user } = useAuth()

  const fetchVideos = useCallback(async () => {
    if (!user?.id) return
    try {
      const response = await axiosClient.get(`/videos?owner_id=${user.id}`)
      setVideos(response.data)
    } catch (err) {
      console.error('Failed to fetch videos:', err)
    } finally {
      setLoading(false)
    }
  }, [user?.id])

  useEffect(() => {
    fetchVideos()
  }, [fetchVideos])

  const handleUploadStarted = (data: PendingUpload) => {
    setPendingUploads((prev) => [...prev, data])
    // Add video to list immediately with processing status
    setVideos((prev) => [
      { id: data.videoId, title: data.title, status: 'processing' },
      ...prev,
    ])
  }

  const handleVideoReady = (videoId: number) => {
    setVideos((prev) =>
      prev.map((v) => (v.id === videoId ? { ...v, status: 'ready' as const } : v))
    )
  }

  const handleVideoFailed = (videoId: number) => {
    setVideos((prev) =>
      prev.map((v) => (v.id === videoId ? { ...v, status: 'failed' as const } : v))
    )
  }

  const handleRemovePending = (taskId: string) => {
    setPendingUploads((prev) => prev.filter((p) => p.taskId !== taskId))
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold text-gray-800">Dashboard</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Upload Video Card */}
        <VideoUploadCard onUploadStarted={handleUploadStarted} />

        {/* Your Videos Card */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="text-xl font-semibold text-gray-800 mb-4">Your Videos</h2>
          
          {/* Processing Tracker */}
          <VideoProcessingTracker
            pending={pendingUploads}
            onVideoReady={handleVideoReady}
            onVideoFailed={handleVideoFailed}
            onRemovePending={handleRemovePending}
          />

          <div className="space-y-3 mt-3">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <svg
                  className="animate-spin h-6 w-6 text-gray-400"
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
              </div>
            ) : videos.length === 0 ? (
              <div className="text-center py-4">
                <div className="w-16 h-10 bg-gray-200 rounded mx-auto mb-2 flex items-center justify-center">
                  <span className="text-gray-400 text-xs">📹</span>
                </div>
                <p className="text-sm font-medium text-gray-700">No videos yet</p>
                <p className="text-xs text-gray-400">Upload your first video</p>
              </div>
            ) : (
              videos.map((video) => (
                <VideoItem
                  key={video.id}
                  id={video.id}
                  title={video.title}
                  status={video.status}
                />
              ))
            )}
          </div>
        </div>

        {/* Semantic Search Card */}
        <SemanticSearchCard ownerId={user?.id} />
      </div>

      {/* Info Section */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <h3 className="text-sm font-medium text-blue-800 mb-1">Getting Started</h3>
        <p className="text-sm text-blue-700">
          Upload a video to automatically transcribe it and enable semantic search across your content.
          Search functionality will be available once you have uploaded videos.
        </p>
      </div>
    </div>
  )
}

export default DashboardPage
