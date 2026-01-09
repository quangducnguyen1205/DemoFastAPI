import { useState, useRef } from 'react'
import axiosClient from '../api/axiosClient'
import { useAuth } from '../context/AuthContext'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Input } from './ui'

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
  const [isDragOver, setIsDragOver] = useState(false)
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

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const droppedFile = e.dataTransfer.files?.[0]
    if (droppedFile && droppedFile.type.startsWith('video/')) {
      setFile(droppedFile)
      setError('')
      setSuccess('')
    } else {
      setError('Please drop a valid video file.')
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
    <Card>
      <CardHeader>
        <CardTitle>Upload Video</CardTitle>
        <CardDescription>Add a new video to transcribe</CardDescription>
      </CardHeader>
      <CardContent>
        {success && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded-xl text-sm flex items-start gap-2">
            <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
            </svg>
            {success}
          </div>
        )}
        
        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-xl text-sm flex items-start gap-2">
            <svg className="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Dropzone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`
              relative border-2 border-dashed rounded-xl p-6 text-center transition-all cursor-pointer
              ${isDragOver 
                ? 'border-brand-400 bg-brand-50' 
                : file 
                  ? 'border-green-300 bg-green-50' 
                  : 'border-app-border hover:border-brand-300 hover:bg-gray-50'
              }
              ${uploading ? 'opacity-50 pointer-events-none' : ''}
            `}
            onClick={() => !uploading && fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="video/*"
              onChange={handleFileChange}
              disabled={uploading}
              className="hidden"
              id="video-file-input"
            />
            
            {file ? (
              <div className="space-y-2">
                <div className="w-12 h-12 bg-green-100 rounded-xl mx-auto flex items-center justify-center">
                  <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 13l4 4L19 7" />
                  </svg>
                </div>
                <p className="font-medium text-gray-900 truncate px-4">{file.name}</p>
                <p className="text-sm text-app-muted">
                  {(file.size / (1024 * 1024)).toFixed(2)} MB
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="w-12 h-12 bg-gray-100 rounded-xl mx-auto flex items-center justify-center">
                  <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                </div>
                <p className="font-medium text-gray-900">
                  Drop your video here
                </p>
                <p className="text-sm text-app-muted">
                  or <span className="text-brand-600">browse</span> to upload
                </p>
              </div>
            )}
          </div>

          {/* Format hint */}
          <p className="text-xs text-app-muted text-center">
            Supports MP4, MOV, AVI, and other common formats
          </p>

          {/* Title Input */}
          <Input
            label="Video Title"
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Enter a descriptive title"
            disabled={uploading}
          />

          {/* Submit Button */}
          <Button
            type="submit"
            className="w-full"
            disabled={!file || !title.trim()}
            loading={uploading}
          >
            Upload Video
          </Button>
        </form>
      </CardContent>
    </Card>
  )
}

export default VideoUploadCard
