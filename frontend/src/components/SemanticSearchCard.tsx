import { useState } from 'react'
import { Link } from 'react-router-dom'
import axiosClient from '../api/axiosClient'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Button, Input, Badge } from './ui'

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
    <Card className="md:col-span-2 lg:col-span-1">
      <CardHeader>
        <CardTitle>Semantic Search</CardTitle>
        <CardDescription>Find moments in your videos</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Search Input */}
          <Input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search your video content..."
            disabled={loading}
            leftIcon={
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            }
          />

          {/* Search Button */}
          <Button
            onClick={handleSearch}
            disabled={!query.trim()}
            loading={loading}
            className="w-full"
          >
            Search
          </Button>

          {/* Error State */}
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl">
              <p className="text-sm text-red-600 flex items-center gap-2">
                <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {error}
              </p>
            </div>
          )}

          {/* Results */}
          {hasSearched && !loading && !error && (
            <div>
              {results.length === 0 ? (
                <div className="text-center py-6 bg-gray-50 rounded-xl border border-app-border">
                  <div className="w-12 h-12 bg-gray-100 rounded-xl mx-auto mb-3 flex items-center justify-center">
                    <svg className="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                  </div>
                  <p className="text-sm font-medium text-gray-900 mb-1">No results found</p>
                  <p className="text-xs text-app-muted">Try a different search query</p>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs text-app-muted">
                    {results.length} result{results.length !== 1 ? 's' : ''} found
                  </p>
                  <div className="space-y-2 max-h-64 overflow-y-auto">
                    {results.map((result) => (
                      <Link
                        key={result.video_id}
                        to={`/videos/${result.video_id}`}
                        className="block p-3 rounded-xl border border-app-border bg-white hover:border-brand-200 hover:bg-brand-50/30 transition-all group"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-gray-900 truncate group-hover:text-brand-700 transition-colors">
                              {result.title}
                            </p>
                            {result.path && (
                              <p className="text-xs text-app-muted mt-1 line-clamp-2">
                                {truncateText(result.path, 120)}
                              </p>
                            )}
                          </div>
                          <Badge variant="info" size="sm" className="flex-shrink-0">
                            {(result.similarity_score * 100).toFixed(1)}%
                          </Badge>
                        </div>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Help Text */}
          {!hasSearched && (
            <div className="text-center py-4">
              <p className="text-sm text-app-muted">
                Search through your video transcripts using natural language
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

export default SemanticSearchCard
