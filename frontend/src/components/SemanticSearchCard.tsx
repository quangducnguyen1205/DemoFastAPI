import { useState } from 'react'
import { Link } from 'react-router-dom'
import axiosClient from '../api/axiosClient'

interface SearchResult {
  video_id: number
  title: string
  path: string | null
  similarity_score: number
}

interface SemanticSearchCardProps {
  ownerId: number | undefined
}

function SemanticSearchCard({ ownerId }: SemanticSearchCardProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasSearched, setHasSearched] = useState(false)

  const handleSearch = async () => {
    if (!query.trim()) {
      setError('Please enter a search query')
      return
    }

    if (!ownerId) {
      setError('User not authenticated')
      return
    }

    setLoading(true)
    setError(null)
    setHasSearched(true)

    try {
      const params = new URLSearchParams({
        q: query.trim(),
        owner_id: String(ownerId),
        k: '10',
      })
      const response = await axiosClient.get<SearchResult[]>(`/videos/search?${params}`)
      setResults(response.data)
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'response' in err) {
        const axiosErr = err as { response?: { data?: { detail?: string } } }
        setError(axiosErr.response?.data?.detail || 'Search failed')
      } else {
        setError('Search failed')
      }
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }

  const truncateText = (text: string, maxLength: number): string => {
    if (text.length <= maxLength) return text
    return text.slice(0, maxLength) + '...'
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 md:col-span-2 lg:col-span-1">
      <h2 className="text-xl font-semibold text-gray-800 mb-4">Semantic Search</h2>
      
      <div className="space-y-4">
        {/* Search Input */}
        <div>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search your video content..."
            disabled={loading}
            className="w-full px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-gray-50 disabled:cursor-not-allowed"
          />
        </div>

        {/* Search Button */}
        <button
          onClick={handleSearch}
          disabled={loading || !query.trim()}
          className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
        >
          {loading ? (
            <>
              <svg
                className="animate-spin h-4 w-4 mr-2"
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
              Searching...
            </>
          ) : (
            <>
              <svg
                className="h-4 w-4 mr-2"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              Search
            </>
          )}
        </button>

        {/* Error State */}
        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-md">
            <p className="text-sm text-red-600">{error}</p>
          </div>
        )}

        {/* Results */}
        {hasSearched && !loading && !error && (
          <div className="mt-4">
            {results.length === 0 ? (
              <div className="text-center py-4">
                <p className="text-sm text-gray-500">No results found for "{query}"</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                <p className="text-xs text-gray-500 mb-2">
                  {results.length} result{results.length !== 1 ? 's' : ''} found
                </p>
                {results.map((result) => (
                  <Link
                    key={result.video_id}
                    to={`/videos/${result.video_id}`}
                    className="block p-3 bg-gray-50 rounded-md hover:bg-gray-100 transition-colors"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0 mr-2">
                        <p className="text-sm font-medium text-gray-800 truncate">
                          {result.title}
                        </p>
                        {result.path && (
                          <p className="text-xs text-gray-500 mt-1">
                            {truncateText(result.path, 120)}
                          </p>
                        )}
                      </div>
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800 flex-shrink-0">
                        {result.similarity_score.toFixed(3)}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Help Text */}
        {!hasSearched && (
          <p className="text-sm text-gray-500 text-center">
            Search through your video transcripts using natural language
          </p>
        )}
      </div>
    </div>
  )
}

export default SemanticSearchCard
