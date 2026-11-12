import { useRef } from 'react'

interface Props {
  fileName: string | null
  pageCount: number
  documentBusy: boolean
  sttActive: boolean
  onFileSelect: (file: File) => void
}

export function DocumentPanel({ fileName, pageCount, documentBusy, sttActive, onFileSelect }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  return (
    <section className="document-panel" aria-labelledby="document-title" aria-busy={documentBusy}>
      <div className="document-heading"><h2 id="document-title">문서</h2><span>{pageCount > 0 ? `${pageCount}페이지` : 'PDF'}</span></div>
      <div className="file-row">
        <span className="file-name" title={fileName ?? undefined}>
          {documentBusy ? '업로드·분석 중…' : (fileName ?? 'PDF 문서를 선택하세요')}
        </span>
        <button
          type="button"
          className="btn ghost"
          disabled={documentBusy || sttActive}
          onClick={() => fileInputRef.current?.click()}
        >
          PDF 업로드
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) onFileSelect(file)
            e.target.value = ''
          }}
        />
      </div>
    </section>
  )
}
