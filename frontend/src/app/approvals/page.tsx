"use client";

import Link from "next/link";
import AppShell from "@/components/shell/AppShell";
import { Err, fmt, link, Loading, td, th } from "@/components/ui";
import { errText, type List } from "@/lib/api";
import { type QuoteApproval, won } from "@/lib/quote";
import { useApi } from "@/lib/useApi";

export type Approval = {
  approval_id: number;
  document_id: number;
  revision_id: string;
  requested_by: number;
  approver_id: number;
  status: string;
  comment: string | null;
  created_at: string;
  decided_at: string | null;
  doc_no: string;
  title: string;
  revision_no: number;
  requested_by_name: string;
  approver_name: string;
};

export default function ApprovalsPage() {
  const { data, error } = useApi<List<Approval>>("/approvals?status=PENDING");
  const quotes = useApi<List<QuoteApproval>>("/quote-approvals?status=PENDING");
  return (
    <AppShell title="승인함">
      <Err text={error && errText(error)} />
      {!data ? (
        !error && <Loading />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className={th}>도면번호</th>
              <th className={th}>제목</th>
              <th className={th}>Rev</th>
              <th className={th}>요청자</th>
              <th className={th}>요청일</th>
              <th className={th}></th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((a) => (
              <tr key={a.approval_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{a.doc_no}</td>
                <td className={td}>{a.title}</td>
                <td className={td}>{a.revision_no}</td>
                <td className={td}>{a.requested_by_name}</td>
                <td className={td}>{fmt(a.created_at)}</td>
                <td className={td}>
                  <Link href={`/approvals/${a.approval_id}`} className={link}>
                    검토
                  </Link>
                </td>
              </tr>
            ))}
            {data.items.length === 0 && (
              <tr>
                <td colSpan={6} className={`${td} text-muted-foreground`}>
                  대기 중인 승인 요청이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      <h2 className="pt-4 text-lg font-semibold text-foreground">견적</h2>
      <Err text={quotes.error && errText(quotes.error)} />
      {!quotes.data ? (
        !quotes.error && <Loading />
      ) : (
        <table className="w-full text-sm" aria-label="견적 승인 대기">
          <thead>
            <tr>
              <th className={th}>견적번호</th>
              <th className={`${th} text-right`}>합계(VAT 포함)</th>
              <th className={th}>요청자</th>
              <th className={th}>요청일</th>
              <th className={th}></th>
            </tr>
          </thead>
          <tbody>
            {quotes.data.items.map((a) => (
              <tr key={a.approval_id} className="border-t border-line">
                <td className={`${td} font-mono`}>{a.quote_no}</td>
                <td className={`${td} text-right font-mono`}>₩{won(a.total_amount)}</td>
                <td className={td}>{a.requested_by_name}</td>
                <td className={td}>{fmt(a.created_at)}</td>
                <td className={td}>
                  <Link href={`/quotes/${a.quote_id}`} className={link}>
                    검토
                  </Link>
                </td>
              </tr>
            ))}
            {quotes.data.items.length === 0 && (
              <tr>
                <td colSpan={5} className={`${td} text-muted-foreground`}>
                  대기 중인 견적 승인 요청이 없습니다.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}
