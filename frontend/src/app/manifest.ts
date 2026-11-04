import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "필기봇 OCR Studio",
    short_name: "필기봇",
    description: "손글씨·인쇄 문서를 검색 가능한 PDF로 변환 (AI OCR은 인터넷 연결 필요)",
    start_url: "/",
    display: "standalone",
    background_color: "#f8f9fb",
    theme_color: "#3b4fc4",
    lang: "ko",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
