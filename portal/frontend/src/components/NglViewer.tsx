"use client";

import { useEffect, useRef } from "react";

type Props = {
  url: string | null;
  fileName?: string | null;
};

const inferExtension = (fileName?: string | null) => {
  if (!fileName) return "pdb";
  const lower = fileName.toLowerCase();
  if (lower.endsWith(".sdf")) return "sdf";
  if (lower.endsWith(".cif")) return "cif";
  return "pdb";
};

export function NglViewer({ url, fileName }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let stage: any;
    async function init() {
      if (!url || !ref.current) return;
      const NGL = await import("ngl");
      stage = new NGL.Stage(ref.current, { backgroundColor: "#050816" });
      const ext = inferExtension(fileName);
      const comp = await stage.loadFile(url, { ext, defaultRepresentation: false });
      if (ext === "sdf") {
        comp.addRepresentation("ball+stick", { multipleBond: true, colorScheme: "element" });
      } else {
        comp.addRepresentation("cartoon", { colorScheme: "chainname" });
      }
      comp.autoView();
    }
    init();
    return () => {
      if (stage) {
        stage.dispose();
      }
    };
  }, [url, fileName]);

  return <div ref={ref} className="h-80 w-full rounded-xl border border-slate-800" />;
}
