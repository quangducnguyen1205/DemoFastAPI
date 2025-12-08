import { useState, useRef } from 'react'
import axiosClient from '../api/axiosClient'
import { useAuth } from '../context/AuthContext'

interface UploadStartedData {
  taskId: string
  videoId: number
  title: string
}

interface VideoUploadCardProps {
  onUploadStarted?: (data: UploadStartedData) => void
}

function VideoUploadCard({ onUploadStarted }: VideoUploadCardProps) {
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [uploading, setUploading] = useState(false)
  const [success, setSuccess] = useState('')
  const [error, setError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { user } = useAuth()

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0]
    if (selectedFile) {
      setFile(selectedFile)
      setError('')
      setSuccess('')
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSuccess('')

    if (!file) {
      setError('Please select a video file.')
      return
    }

    if (!title.trim()) {
      setError('Please enter a title.')
      return
    }

    setUploading(true)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('title', title.trim())
      if (user?.id) {
        formData.append('owner_id', String(user.id))
      }

      const uploadedTitle = title.trim()

      const response = await axiosClient.post('/videos/upload', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      })

      const { task_id, video_id } = response.data

      setSuccess('Video uploaded successfully! Processing will begin shortly.')
      setFile(null)
      setTitle('')
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // Notify parent about the upload
      if (onUploadStarted) {
        onUploadStarted({
          taskId: task_id,
          videoId: video_id,
          title: uploadedTitle,
        })
      }
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'response' in err) {
        const axiosError = err as { response?: { data?: { detail?: string } } }
        setError(axiosError.response?.data?.detail || 'Upload failed. Please try again.')
      } else {
        setError('Upload failed. Please try again.')
      }
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h2 className="text-xl font-semibold text-gray-800 mb-4">Upload Video</h2>
      
      {success && (
        <div className="mb-4 p-3 bg-green-100 border border-green-400 text-green-700 rounded-md text-sm">
          {success}
        </div>
      )}
      
      {error && (
        <div className="mb-4 p-3 bg-red-100 border border-red-400 text-red-700 rounded-md text-sm">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* File Input */}
        <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center">
          <div className="text-gray-400 mb-2">
            <svg
              className="mx-auto h-10 w-10"
              stroke="currentColor"
              fill="none"
              viewBox="0 0 48 48"
              aria-hidden="true"
            >
              <path
                d="M28 8H12a4 4 0 00-4 4v20m32-12v8m0 0v8a4 4 0 01-4 4H12a4 4 0 01-4-4v-4m32-4l-3.172-3.172a4 4 0 00-5.656 0L28 28M8 32l9.172-9.172a4 4 0 015.656 0L28 28m0 0l4 4m4-24h8m-4-4v8m-12 4h.02"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            onChange={handleFileChange}
            disabled={uploading}
            className="hidden"
            id="video-file-input"
          />
          <label
            htmlFor="video-file-input"
            className={`cursor-pointer text-sm ${uploading ? 'text-gray-400' : 'text-blue-600 hover:text-blue-700'}`}
          >
            {file ? file.name : 'Click to select a video file'}
          </label>
          {file && (
            <p className="text-xs text-gray-400 mt-1">
              {(file.size / (1024 * 1024)).toFixed(2)} MB
            </p>
          )}
        </div>

        {/* Title Input */}
        <div>
          <label htmlFor="video-title" className="block text-sm font-medium text-gray-700 mb-1">
            Video Title
          </label>
          <input
            type="text"
            id="video-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Enter video title"
            disabled={uploading}
            className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-50 disabled:cursor-not-allowed"
          />
        </div>

        {/* Submit Button */}
        <button
          type="submit"
          disabled={uploading || !file || !title.trim()}
          className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {uploading ? 'Uploading...' : 'Upload Video'}
        </button>
      </form>
    </div>
  )
}

export default VideoUploadCard
