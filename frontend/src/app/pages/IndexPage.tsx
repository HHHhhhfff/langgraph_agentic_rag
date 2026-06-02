import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { StatusBadge } from "../components/shared/StatusBadge";
import { Database, RefreshCw, Trash2, AlertTriangle } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "../components/ui/alert-dialog";
import { ScrollArea } from "../components/ui/scroll-area";

export function IndexPage() {
  const [isRebuilding, setIsRebuilding] = useState(false);

  return (
    <div className="h-full flex flex-col">
      <div className="p-6 border-b">
        <h1 className="text-2xl font-semibold">索引管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理 Qdrant 集合、命名向量与本地 BM25/page/table 索引
        </p>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-6 space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Qdrant 集合</CardTitle>
                <StatusBadge status="success" label="已连接" />
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs text-muted-foreground mb-1">
                    集合名称
                  </div>
                  <div className="font-mono text-sm">agentic_rag_docs</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground mb-1">
                    向量点数
                  </div>
                  <div className="font-mono text-sm">208 points</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground mb-1">
                    状态
                  </div>
                  <Badge variant="outline" className="text-xs">
                    Green
                  </Badge>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground mb-1">
                    最近更新
                  </div>
                  <div className="text-sm">2026-05-30 18:49</div>
                </div>
              </div>

              <div className="pt-4 border-t">
                <div className="text-sm font-medium mb-3">命名向量</div>
                <div className="space-y-3">
                  {[
                    { name: "text", model: "qwen3-vl-embedding", dim: 1024, count: 140 },
                    { name: "table", model: "qwen3-vl-embedding", dim: 1024, count: 63 },
                    { name: "image", model: "qwen3-vl-embedding", dim: 1024, count: 5 },
                  ].map((vector) => (
                    <div
                      key={vector.name}
                      className="flex items-center justify-between p-3 bg-muted rounded"
                    >
                      <div className="space-y-1">
                        <div className="font-mono text-sm">{vector.name}</div>
                        <div className="text-xs text-muted-foreground">
                          {vector.model} · {vector.dim}d
                        </div>
                      </div>
                      <Badge variant="secondary">{vector.count} 个向量</Badge>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">索引操作</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button
                className="w-full justify-start"
                variant="outline"
                onClick={() => {
                  setIsRebuilding(true);
                  setTimeout(() => setIsRebuilding(false), 3000);
                }}
                disabled={isRebuilding}
              >
                <RefreshCw className={`h-4 w-4 mr-2 ${isRebuilding ? "animate-spin" : ""}`} />
                重建索引
              </Button>

              <Button className="w-full justify-start" variant="outline">
                <Database className="h-4 w-4 mr-2" />
                仅写入本地索引
              </Button>

              <Button className="w-full justify-start" variant="outline">
                <AlertTriangle className="h-4 w-4 mr-2" />
                检查索引健康
              </Button>

              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    className="w-full justify-start text-red-600 hover:text-red-700"
                    variant="outline"
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    重建集合（危险）
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      确认重建集合？
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      这会删除当前的 agentic_rag_docs 及其中全部向量。
                      该操作不可撤销，需要重新执行文档入库才能恢复索引。
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>取消</AlertDialogCancel>
                    <AlertDialogAction className="bg-red-600 hover:bg-red-700">
                      删除并重建
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">构建日志</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {[
                  {
                    time: "2026-05-30 18:49:42",
                    message: "索引构建完成：upserted=208，failed_files=0",
                    status: "success" as const,
                  },
                  {
                    time: "2026-05-30 18:49:28",
                    message: "写入 Qdrant collection agentic_rag_docs",
                    status: "success" as const,
                  },
                  {
                    time: "2026-05-30 18:49:15",
                    message: "生成 text/table/image named vectors",
                    status: "success" as const,
                  },
                  {
                    time: "2026-05-30 18:48:58",
                    message: "启动 inspect_then_build_index 流程",
                    status: "success" as const,
                  },
                ].map((log) => (
                  <div
                    key={`${log.time}-${log.message}`}
                    className="flex items-start gap-3 py-2 border-b last:border-0 text-xs"
                  >
                    <StatusBadge status={log.status} />
                    <div className="flex-1 space-y-0.5">
                      <div>{log.message}</div>
                      <div className="text-muted-foreground font-mono">
                        {log.time}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">失败文件</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-sm text-muted-foreground text-center py-4">
                当前没有失败文件
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}
