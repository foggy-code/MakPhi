import { useMemo, useState } from "react";

// Colori per classe chimica (schema in stile Clustal X).
const CLASS_COLOR: Record<string, string> = {};
const groups: [string, string][] = [
  ["AILMFWV", "#5b8def"],
  ["KR", "#ef5350"],
  ["ED", "#c35bd6"],
  ["NQST", "#34c759"],
  ["C", "#f48fb1"],
  ["G", "#f5a05a"],
  ["P", "#d4c22b"],
  ["HY", "#26b5b5"],
];
groups.forEach(([letters, color]) => letters.split("").forEach((l) => (CLASS_COLOR[l] = color)));

interface Props {
  ids: string[];
  aligned: string[];
  conservation?: string;
  width?: number;
}

export function AlignmentView({ ids, aligned, conservation, width = 60 }: Props) {
  const [colored, setColored] = useState(true);
  const [block, setBlock] = useState(width);
  const length = aligned[0]?.length ?? 0;
  const cells = length * aligned.length;
  const canColor = cells <= 80_000;
  const identical = useMemo(() => {
    const out: boolean[] = [];
    for (let c = 0; c < length; c++) {
      const col = aligned.map((s) => s[c]);
      out.push(col[0] !== "-" && col.every((x) => x === col[0]));
    }
    return out;
  }, [aligned, length]);
  // numero dell'ultimo residuo (senza gap) di ogni blocco, come nel formato Clustal
  const ends = useMemo(() => {
    const res: number[][] = aligned.map(() => []);
    aligned.forEach((s, r) => {
      let n = 0;
      for (let c = 0; c < length; c++) {
        if (s[c] !== "-" && s[c] !== ".") n++;
        if ((c + 1) % block === 0 || c === length - 1) res[r].push(n);
      }
    });
    return res;
  }, [aligned, length, block]);

  if (!aligned.length) return null;
  const blocks = [];
  for (let b = 0; b * block < length; b++) {
    const from = b * block;
    const to = Math.min(length, from + block);
    blocks.push(
      <div className="aln-block" key={b}>
        <div className="aln-row aln-ruler">
          <div className="aln-name" />
          <div className="aln-seq">
            {Array.from({ length: to - from }, (_, i) => {
              const col = from + i + 1;
              return <span key={i}>{col % 10 === 0 ? "|" : col % 5 === 0 ? "·" : " "}</span>;
            })}
            <span style={{ width: "auto", paddingLeft: 8 }}>{to}</span>
          </div>
        </div>
        {aligned.map((seq, r) => (
          <div className="aln-row" key={r}>
            <div className="aln-name" title={ids[r]}>{ids[r]}</div>
            <div className="aln-seq">
              {seq
                .slice(from, to)
                .split("")
                .map((ch, i) => {
                  const col = from + i;
                  const color = colored && canColor ? CLASS_COLOR[ch.toUpperCase()] : undefined;
                  return (
                    <span
                      key={i}
                      style={
                        color
                          ? { background: `${color}${identical[col] ? "55" : "26"}`, color: identical[col] ? "var(--text)" : undefined, fontWeight: identical[col] ? 700 : 400 }
                          : ch === "-" ? { color: "var(--muted)" } : undefined
                      }
                    >
                      {ch}
                    </span>
                  );
                })}
              <span style={{ width: "auto", paddingLeft: 8, color: "var(--muted)" }}>{ends[r][b]}</span>
            </div>
          </div>
        ))}
        {conservation && (
          <div className="aln-row">
            <div className="aln-name muted">consenso</div>
            <div className="aln-seq muted">
              {conservation.slice(from, to).split("").map((c, i) => (
                <span key={i}>{c}</span>
              ))}
            </div>
          </div>
        )}
      </div>,
    );
  }
  return (
    <div className="stack-sm">
      <div className="row small">
        <label className="check">
          <input type="checkbox" checked={colored && canColor} disabled={!canColor} onChange={(e) => setColored(e.target.checked)} />
          Colora per classe chimica
        </label>
        <span className="muted">Colonne identiche in evidenza · lunghezza {length}</span>
        <label className="check right">
          Larghezza blocco
          <select className="input" style={{ width: 80, minHeight: 28, padding: "2px 6px" }} value={block} onChange={(e) => setBlock(Number(e.target.value))}>
            {[50, 60, 80, 100, 150].map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="aln">{blocks}</div>
      {colored && canColor && (
        <div className="legend">
          {[["Idrofobici", "#5b8def"], ["Positivi", "#ef5350"], ["Negativi", "#c35bd6"], ["Polari", "#34c759"], ["Cys", "#f48fb1"], ["Gly", "#f5a05a"], ["Pro", "#d4c22b"], ["His/Tyr", "#26b5b5"]].map(([l, c]) => (
            <span key={l}><i style={{ background: c }} />{l}</span>
          ))}
        </div>
      )}
    </div>
  );
}
