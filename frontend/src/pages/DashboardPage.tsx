import { useState, useEffect, useCallback } from 'react'
import VideoUploadCard from '../components/VideoUploadCard'
import VideoProcessingTracker from '../components/VideoProcessingTracker'
import VideoItem from '../components/VideoItem'
import SemanticSearchCard from '../components/SemanticSearchCard'
import axiosClient from '../api/axiosClient'
import { useAuth } from '../context/AuthContext'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Skeleton } from '../components/ui'

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
    <div className="space-y-8">
      {/* Page Header */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="mt-1 text-app-muted">
          Manage your videos and search through your content
        </p>
      </div>
      
      {/* Main Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {/* Upload Video Card */}
        <VideoUploadCard onUploadStarted={handleUploadStarted} />

        {/* Your Videos Card */}
        <Card>
          <CardHeader>
            <CardTitle>Your Videos</CardTitle>
            <CardDescription>View and manage uploaded content</CardDescription>
          </CardHeader>
          <CardContent>
            {/* Processing Tracker */}
            <VideoProcessingTracker
              pending={pendingUploads}
              onVideoReady={handleVideoReady}
              onVideoFailed={handleVideoFailed}
              onRemovePending={handleRemovePending}
            />

            <div className="space-y-3">
              {loading ? (
                // Skeleton loading state
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="flex items-center gap-3 p-3 rounded-xl border border-app-border">
                      <Skeleton width={48} height={48} rounded="xl" />
                      <div className="flex-1 space-y-2">
                        <Skeleton height={16} width="70%" rounded="md" />
                        <Skeleton height={12} width="30%" rounded="md" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : videos.length === 0 ? (
                // Empty state
                <div className="text-center py-8">
                  <div className="w-16 h-16 bg-gray-100 rounded-2xl mx-auto mb-4 flex items-center justify-center">
                    <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <p className="font-medium text-gray-900 mb-1">No videos yet</p>
                  <p className="text-sm text-app-muted">
                    Upload your first video to get started
                  </p>
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
          </CardContent>
        </Card>

        {/* Semantic Search Card */}
        <SemanticSearchCard ownerId={user?.id} />
      </div>

      {/* Info Section */}
      <Card className="bg-brand-50 border-brand-100">
        <CardContent className="flex items-start gap-4">
          <div className="w-10 h-10 bg-brand-100 rounded-xl flex items-center justify-center flex-shrink-0">
            <svg className="w-5 h-5 text-brand-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <h3 className="font-medium text-brand-900 mb-1">Getting Started</h3>
            <p className="text-sm text-brand-700">
              Upload a video to automatically transcribe it and enable semantic search across your content.
              Search functionality will be available once you have uploaded videos.
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

export default DashboardPage
