"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlaskConical, History, PenLine, Plus, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "새 작업", icon: Plus },
  { href: "/history", label: "작업 기록", icon: History },
  { href: "/evaluate", label: "정확도 평가", icon: FlaskConical },
  { href: "/settings", label: "설정", icon: Settings },
];

export function AppHeader() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-40 border-b bg-card/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <PenLine className="size-4" />
          </span>
          <span className="hidden sm:inline">필기봇 OCR Studio</span>
          <span className="sm:hidden">필기봇</span>
        </Link>
        <nav className="ml-auto flex items-center gap-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                aria-label={label}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                  active && "bg-muted text-foreground",
                )}
              >
                <Icon className="size-4" />
                <span className="hidden md:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
