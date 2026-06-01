import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "서버리스 구조 예측 허브",
  description: "AlphaFold2, DiffDock, PHASTEST 작업을 한 곳에서 관리하세요.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body className="bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}

