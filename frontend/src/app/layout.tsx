import type { Metadata, Viewport } from "next";
import { Toaster } from "sonner";
import { AppHeader } from "@/components/common/app-header";
import { ServiceWorker } from "@/components/common/service-worker";
import "./globals.css";

export const metadata: Metadata = {
  title: "필기봇 OCR Studio",
  description: "손글씨·인쇄 문서를 검색 가능한 PDF로 변환하는 AI OCR",
  applicationName: "필기봇 OCR Studio",
  appleWebApp: { capable: true, title: "필기봇", statusBarStyle: "default" },
  icons: { icon: "/icons/icon-192.png", apple: "/icons/apple-touch-icon.png" },
};

export const viewport: Viewport = {
  themeColor: "#3b4fc4",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-dvh antialiased">
        <AppHeader />
        <main>{children}</main>
        <Toaster position="top-center" richColors closeButton />
        <ServiceWorker />
      </body>
    </html>
  );
}
