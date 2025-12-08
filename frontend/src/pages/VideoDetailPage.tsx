import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import axiosClient from '../api/axiosClient'
import Layout from '../components/Layout'

interface Video {
  id: number
  title: string
  status: string
}

interface TranscriptSegment {
  id: number
  video_id: number
  segment_index: number
  text: string
  created_at: string
}

function VideoDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [video, setVideo] = useState<Video | null>(null)
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return

    const fetchData = async () => {
      setLoading(true)
      setError(null)

      try {
        const [videoRes, transcriptRes] = await Promise.all([
          axiosClient.get<Video>(`/videos/${id}`),
          axiosClient.get<TranscriptSegment[]>(`/videos/${id}/transcript`),
        ])

        setVideo(videoRes.data)
        setTranscript(transcriptRes.data)
      } catch (err: unknown) {
        if (err instanceof Error) {
          setError(err.message)
        } else {
          setError('Failed to load video data')
        }
      } finally {
        setLoading(false)
      }
    }

    fetchData()
  }, [id])

  if (loading) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto py-8 px-4">
          <div className="bg-white rounded-lg shadow p-8">
            <div className="flex items-center justify-center">
              <svg
                className="animate-spin h-8 w-8 text-indigo-600"
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
              <span className="ml-3 text-gray-600">Loading video...</span>
            </div>
          </div>
        </div>
      </Layout>
    )
  }

  if (error) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto py-8 px-4">
          <div className="bg-white rounded-lg shadow p-8">
            <div className="text-center">
              <div className="text-red-500 text-lg font-medium mb-2">Error</div>
              <p className="text-gray-600 mb-4">{error}</p>
              <Link
                to="/dashboard"
                className="text-indigo-600 hover:text-indigo-800 font-medium"
              >
                ← Back to Dashboard
              </Link>
            </div>
          </div>
        </div>
      </Layout>
    )
  }

  if (!video) {
    return (
      <Layout>
        <div className="max-w-3xl mx-auto py-8 px-4">
          <div className="bg-white rounded-lg shadow p-8">
            <div className="text-center">
              <p className="text-gray-600 mb-4">Video not found</p>
              <Link
                to="/dashboard"
                className="text-indigo-600 hover:text-indigo-800 font-medium"
              >
                ← Back to Dashboard
              </Link>
            </div>
          </div>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="max-w-3xl mx-auto py-8 px-4">
        {/* Back link */}
        <Link
          to="/dashboard"
          className="inline-flex items-center text-indigo-600 hover:text-indigo-800 font-medium mb-6"
        >
          <svg
            className="h-4 w-4 mr-1"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M15 19l-7-7 7-7"
            />
          </svg>
          Back to Dashboard
        </Link>

        {/* Video Card */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="flex items-center mb-4">
            <div className="w-16 h-16 bg-gray-200 rounded-lg flex items-center justify-center mr-4">
              <span className="text-3xl">📹</span>
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{video.title}</h1>
              <span className="inline-block mt-1 px-2 py-1 bg-gray-100 text-gray-600 rounded text-sm">
                {video.status}
              </span>
            </div>
          </div>
        </div>

        {/* Transcript Card */}
        <div className="bg-white rounded-lg shadow">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Transcript</h2>
            <p className="text-sm text-gray-500 mt-1">
              {transcript.length} segment{transcript.length !== 1 ? 's' : ''}
            </p>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {transcript.length === 0 ? (
              <div className="px-6 py-8 text-center text-gray-500">
                No transcript available for this video.
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {transcript.map((segment, index) => (
                  <li key={segment.id} className="px-6 py-3 hover:bg-gray-50">
                    <div className="flex">
                      <span className="text-gray-400 font-mono text-sm w-12 flex-shrink-0">
                        #{index + 1}
                      </span>
                      <p className="text-gray-700 text-sm leading-relaxed">
                        {segment.text}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </Layout>
  )
}

export default VideoDetailPage
