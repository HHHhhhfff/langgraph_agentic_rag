import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { ScrollArea } from "../components/ui/scroll-area";
import { ModalityBadge } from "../components/shared/ModalityBadge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Search,
  ArrowRight,
  TrendingUp,
  TrendingDown,
  Minus,
  X,
  CheckCircle2,
} from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

interface ChunkScore {
  id: string;
  source: string;
  page: number;
  chunk_index: number;
  modality: "text" | "table" | "formula" | "image";
  channel: string;
  initial_rank?: number;
  initial_score?: number;
  rerank_rank?: number;
  rerank_score?: number;
  final_rank?: number;
  final_score?: number;
  status: "selected" | "dropped" | "filtered" | "agent_dropped";
}

export function RetrievalPage() {
  const [selectedQuery, setSelectedQuery] = useState("query_20260601_171610");
  const [modalityFilter, setModalityFilter] = useState("all");

  const mockQueries = [
    {
      id: "query_20260601_171610",
      query: "Q=3 对应哪种曲线？n 表示什么？",
      route: "image_first",
      evidence_ok: true,
      time: "2 分钟前",
    },
    {
      id: "query_20260601_171409",
      query: "这个项目支持哪些检索过滤能力？",
      route: "hybrid_search",
      evidence_ok: true,
      time: "12 分钟前",
    },
    {
      id: "query_20260601_170234",
      query: "表格和公式会如何参与关系扩展？",
      route: "formula_table",
      evidence_ok: false,
      time: "1 小时前",
    },
  ];

  const mockChunks: ChunkScore[] = [
    {
      id: "chunk_1",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      page: 3,
      chunk_index: 12,
      modality: "text",
      channel: "vector",
      initial_rank: 1,
      initial_score: 0.86,
      rerank_rank: 1,
      rerank_score: 0.91,
      final_rank: 1,
      final_score: 0.91,
      status: "selected",
    },
    {
      id: "chunk_2",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      page: 1,
      chunk_index: 7,
      modality: "formula",
      channel: "relationship",
      initial_rank: 4,
      initial_score: 0.62,
      rerank_rank: 2,
      rerank_score: 0.84,
      final_rank: 2,
      final_score: 0.84,
      status: "selected",
    },
    {
      id: "chunk_3",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      page: 3,
      chunk_index: 2,
      modality: "image",
      channel: "image",
      initial_rank: 2,
      initial_score: 0.78,
      rerank_rank: 3,
      rerank_score: 0.78,
      final_rank: 3,
      final_score: 0.78,
      status: "selected",
    },
    {
      id: "chunk_4",
      source: "2409.18430_Energy_Levels.pdf",
      page: 8,
      chunk_index: 5,
      modality: "text",
      channel: "bm25",
      initial_rank: 3,
      initial_score: 0.71,
      rerank_rank: 7,
      rerank_score: 0.42,
      status: "dropped",
    },
    {
      id: "chunk_5",
      source: "测试文档.pdf",
      page: 2,
      chunk_index: 1,
      modality: "table",
      channel: "table",
      initial_rank: 5,
      initial_score: 0.68,
      rerank_rank: 4,
      rerank_score: 0.55,
      status: "agent_dropped",
    },
    {
      id: "chunk_6",
      source: "Linux常用命令整理/测试表格.md",
      page: 1,
      chunk_index: 8,
      modality: "text",
      channel: "hybrid",
      initial_rank: 6,
      initial_score: 0.65,
      status: "filtered",
    },
  ];

  const stageStats = {
    initial_retrieval: { label: "初始召回", total: 12, text: 7, table: 2, formula: 1, image: 2 },
    rerank: { label: "Rerank 后", total: 7, text: 3, table: 1, formula: 1, image: 2 },
    final_after_retry: { label: "局部重检后", total: 7, text: 3, table: 1, formula: 1, image: 2 },
    final_output: { label: "最终引用", total: 3, text: 1, table: 0, formula: 1, image: 1 },
  };

  const getRankChange = (chunk: ChunkScore) => {
    if (!chunk.initial_rank || !chunk.rerank_rank) return null;
    const change = chunk.initial_rank - chunk.rerank_rank;
    if (change > 0)
      return <TrendingUp className="h-3.5 w-3.5 text-green-600" />;
    if (change < 0)
      return <TrendingDown className="h-3.5 w-3.5 text-red-600" />;
    return <Minus className="h-3.5 w-3.5 text-gray-400" />;
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "selected":
        return <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />;
      case "dropped":
        return <X className="h-3.5 w-3.5 text-red-600" />;
      case "filtered":
        return <Minus className="h-3.5 w-3.5 text-gray-400" />;
      case "agent_dropped":
        return <X className="h-3.5 w-3.5 text-orange-600" />;
      default:
        return null;
    }
  };

  return (
    <div className="h-full flex">
      <div className="w-80 border-r bg-card">
        <div className="p-4 border-b">
          <h2 className="text-sm font-semibold mb-3">问答运行记录</h2>
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
            <Input placeholder="搜索问题或 query_id" className="pl-8 h-8 text-xs" />
          </div>
        </div>

        <ScrollArea className="h-[calc(100vh-10rem)]">
          <div className="p-2 space-y-1">
            {mockQueries.map((q) => (
              <button
                key={q.id}
                onClick={() => setSelectedQuery(q.id)}
                className={`w-full text-left p-3 rounded border transition-colors ${
                  selectedQuery === q.id
                    ? "bg-primary/10 border-primary"
                    : "bg-card hover:bg-muted border-transparent"
                }`}
              >
                <div className="space-y-2">
                  <div className="text-xs font-medium line-clamp-2">{q.query}</div>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="text-xs">
                      {q.route}
                    </Badge>
                    {q.evidence_ok ? (
                      <CheckCircle2 className="h-3 w-3 text-green-600" />
                    ) : (
                      <X className="h-3 w-3 text-red-600" />
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground font-mono">
                    {q.id}
                  </div>
                  <div className="text-xs text-muted-foreground">{q.time}</div>
                </div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </div>

      <div className="flex-1 flex flex-col">
        <div className="p-6 border-b">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-2xl font-semibold">检索诊断</h1>
              <p className="text-sm text-muted-foreground mt-1">
                对比 initial_retrieval、rerank、final_after_retry 与 final_output 的排名变化
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Select value={modalityFilter} onValueChange={setModalityFilter}>
                <SelectTrigger className="w-32 h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部模态</SelectItem>
                  <SelectItem value="text">文本</SelectItem>
                  <SelectItem value="table">表格</SelectItem>
                  <SelectItem value="formula">公式</SelectItem>
                  <SelectItem value="image">图片</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <Tabs defaultValue="chunks" className="flex-1 flex flex-col">
          <div className="border-b px-6">
            <TabsList className="h-10">
              <TabsTrigger value="chunks" className="text-xs">
                Chunk 列表
              </TabsTrigger>
              <TabsTrigger value="compare" className="text-xs">
                阶段对比
              </TabsTrigger>
              <TabsTrigger value="flow" className="text-xs">
                排名流转
              </TabsTrigger>
              <TabsTrigger value="citations" className="text-xs">
                引用分析
              </TabsTrigger>
              <TabsTrigger value="raw" className="text-xs">
                原始 JSON
              </TabsTrigger>
            </TabsList>
          </div>

          <ScrollArea className="flex-1">
            <div className="p-6">
              <TabsContent value="chunks" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">召回 Chunk</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-12">状态</TableHead>
                          <TableHead className="w-24">模态</TableHead>
                          <TableHead>来源</TableHead>
                          <TableHead className="w-20">页码</TableHead>
                          <TableHead className="w-24">通道</TableHead>
                          <TableHead className="w-20 text-right">初始</TableHead>
                          <TableHead className="w-12"></TableHead>
                          <TableHead className="w-20 text-right">Rerank</TableHead>
                          <TableHead className="w-20 text-right">最终</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {mockChunks.map((chunk) => (
                          <TableRow key={chunk.id}>
                            <TableCell>{getStatusIcon(chunk.status)}</TableCell>
                            <TableCell>
                              <ModalityBadge modality={chunk.modality} />
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {chunk.source}
                            </TableCell>
                            <TableCell className="text-xs">
                              第 {chunk.page} 页
                            </TableCell>
                            <TableCell>
                              <Badge variant="secondary" className="text-xs">
                                {chunk.channel}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="space-y-0.5">
                                {chunk.initial_rank && (
                                  <div className="text-xs font-mono">
                                    #{chunk.initial_rank}
                                  </div>
                                )}
                                {chunk.initial_score && (
                                  <div className="text-xs text-muted-foreground font-mono">
                                    {chunk.initial_score.toFixed(2)}
                                  </div>
                                )}
                              </div>
                            </TableCell>
                            <TableCell>{getRankChange(chunk)}</TableCell>
                            <TableCell className="text-right">
                              <div className="space-y-0.5">
                                {chunk.rerank_rank && (
                                  <div className="text-xs font-mono">
                                    #{chunk.rerank_rank}
                                  </div>
                                )}
                                {chunk.rerank_score && (
                                  <div className="text-xs text-muted-foreground font-mono">
                                    {chunk.rerank_score.toFixed(2)}
                                  </div>
                                )}
                              </div>
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="space-y-0.5">
                                {chunk.final_rank && (
                                  <div className="text-xs font-mono">
                                    #{chunk.final_rank}
                                  </div>
                                )}
                                {chunk.final_score && (
                                  <div className="text-xs text-muted-foreground font-mono">
                                    {chunk.final_score.toFixed(2)}
                                  </div>
                                )}
                              </div>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="compare" className="mt-0 space-y-4">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">阶段对比</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-4">
                      {Object.entries(stageStats).map(([stage, stats]) => (
                        <div key={stage} className="space-y-2">
                          <div className="flex items-center justify-between">
                            <div className="text-sm font-medium">{stats.label}</div>
                            <Badge variant="outline" className="font-mono">
                              {stats.total} chunks
                            </Badge>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 h-8 bg-muted rounded flex items-center overflow-hidden">
                              <div
                                className="h-full bg-blue-500/80 flex items-center justify-center text-xs text-white font-medium"
                                style={{ width: `${(stats.text / stats.total) * 100}%` }}
                              >
                                {stats.text > 0 && stats.text}
                              </div>
                              <div
                                className="h-full bg-green-500/80 flex items-center justify-center text-xs text-white font-medium"
                                style={{ width: `${(stats.table / stats.total) * 100}%` }}
                              >
                                {stats.table > 0 && stats.table}
                              </div>
                              <div
                                className="h-full bg-purple-500/80 flex items-center justify-center text-xs text-white font-medium"
                                style={{ width: `${(stats.formula / stats.total) * 100}%` }}
                              >
                                {stats.formula > 0 && stats.formula}
                              </div>
                              <div
                                className="h-full bg-orange-500/80 flex items-center justify-center text-xs text-white font-medium"
                                style={{ width: `${(stats.image / stats.total) * 100}%` }}
                              >
                                {stats.image > 0 && stats.image}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-3 text-xs">
                            <div className="flex items-center gap-1">
                              <div className="w-3 h-3 bg-blue-500/80 rounded" />
                              <span>文本：{stats.text}</span>
                            </div>
                            <div className="flex items-center gap-1">
                              <div className="w-3 h-3 bg-green-500/80 rounded" />
                              <span>表格：{stats.table}</span>
                            </div>
                            <div className="flex items-center gap-1">
                              <div className="w-3 h-3 bg-purple-500/80 rounded" />
                              <span>公式：{stats.formula}</span>
                            </div>
                            <div className="flex items-center gap-1">
                              <div className="w-3 h-3 bg-orange-500/80 rounded" />
                              <span>图片：{stats.image}</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="flow" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">排名流转</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-6">
                      <div className="flex items-start gap-4">
                        <div className="flex-1 space-y-2">
                          <div className="text-xs font-medium text-center text-muted-foreground">
                            初始召回
                          </div>
                          {mockChunks
                            .filter((c) => c.initial_rank)
                            .sort((a, b) => a.initial_rank! - b.initial_rank!)
                            .map((chunk) => (
                              <div
                                key={chunk.id}
                                className="p-2 bg-muted rounded border text-xs flex items-center justify-between"
                              >
                                <div className="flex items-center gap-2">
                                  <span className="font-mono">#{chunk.initial_rank}</span>
                                  <ModalityBadge modality={chunk.modality} />
                                </div>
                                <span className="font-mono text-muted-foreground">
                                  {chunk.initial_score?.toFixed(2)}
                                </span>
                              </div>
                            ))}
                        </div>

                        <div className="flex items-center justify-center pt-8">
                          <ArrowRight className="h-5 w-5 text-muted-foreground" />
                        </div>

                        <div className="flex-1 space-y-2">
                          <div className="text-xs font-medium text-center text-muted-foreground">
                            Rerank 后
                          </div>
                          {mockChunks
                            .filter((c) => c.rerank_rank)
                            .sort((a, b) => a.rerank_rank! - b.rerank_rank!)
                            .map((chunk) => (
                              <div
                                key={chunk.id}
                                className={`p-2 rounded border text-xs flex items-center justify-between ${
                                  chunk.status === "selected"
                                    ? "bg-green-50 border-green-200"
                                    : "bg-muted"
                                }`}
                              >
                                <div className="flex items-center gap-2">
                                  <span className="font-mono">#{chunk.rerank_rank}</span>
                                  <ModalityBadge modality={chunk.modality} />
                                </div>
                                <span className="font-mono text-muted-foreground">
                                  {chunk.rerank_score?.toFixed(2)}
                                </span>
                              </div>
                            ))}
                        </div>

                        <div className="flex items-center justify-center pt-8">
                          <ArrowRight className="h-5 w-5 text-muted-foreground" />
                        </div>

                        <div className="flex-1 space-y-2">
                          <div className="text-xs font-medium text-center text-muted-foreground">
                            最终引用
                          </div>
                          {mockChunks
                            .filter((c) => c.final_rank)
                            .sort((a, b) => a.final_rank! - b.final_rank!)
                            .map((chunk) => (
                              <div
                                key={chunk.id}
                                className="p-2 bg-primary/10 border-primary/20 rounded border text-xs flex items-center justify-between"
                              >
                                <div className="flex items-center gap-2">
                                  <span className="font-mono">#{chunk.final_rank}</span>
                                  <ModalityBadge modality={chunk.modality} />
                                </div>
                                <span className="font-mono text-muted-foreground">
                                  {chunk.final_score?.toFixed(2)}
                                </span>
                              </div>
                            ))}
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="citations" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">引用分析</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-4">
                      {mockChunks
                        .filter((c) => c.status === "selected")
                        .map((chunk, index) => (
                          <div
                            key={chunk.id}
                            className="flex items-start gap-3 p-3 border rounded"
                          >
                            <Badge variant="outline" className="font-mono">
                              [{index + 1}]
                            </Badge>
                            <div className="flex-1 space-y-2">
                              <div className="flex items-center gap-2">
                                <ModalityBadge modality={chunk.modality} />
                                <span className="text-xs font-mono">
                                  {chunk.source}
                                </span>
                                <span className="text-xs text-muted-foreground">
                                  第 {chunk.page} 页
                                </span>
                              </div>
                              <div className="flex items-center gap-2">
                                <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                                <span className="text-xs text-green-700">
                                  引用可回溯到当前上下文
                                </span>
                              </div>
                            </div>
                          </div>
                        ))}
                    </div>
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="raw" className="mt-0">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">原始 JSON 输出</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <pre className="text-xs bg-muted p-4 rounded overflow-auto max-h-[600px] font-mono">
                      {JSON.stringify({ query_id: selectedQuery, chunks: mockChunks }, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              </TabsContent>
            </div>
          </ScrollArea>
        </Tabs>
      </div>
    </div>
  );
}
