import * as pdfjsLib from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

export type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist'

export async function loadPdf(file: File) {
  const data = new Uint8Array(await file.arrayBuffer())
  return pdfjsLib.getDocument({ data }).promise
}
