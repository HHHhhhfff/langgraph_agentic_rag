import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
import { ScrollArea } from "../components/ui/scroll-area";
import { Badge } from "../components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";

export function SettingsPage() {
  return (
    <div className="h-full flex flex-col">
      <div className="p-6 border-b">
        <h1 className="text-2xl font-semibold">设置</h1>
        <p className="text-sm text-muted-foreground mt-1">
          这里仅保留后续会接入后端的运行开关，占位项暂不可操作
        </p>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-6 space-y-6 max-w-3xl">
          <Card className="border-yellow-200 bg-yellow-50/70">
            <CardContent className="pt-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-yellow-900">
                    当前设置页尚未接入后端接口
                  </div>
                  <p className="text-xs text-yellow-800 mt-1 leading-relaxed">
                    运行配置继续由后端 .env 管理。下面的开关会在后端 API
                    接好后再启用。
                  </p>
                </div>
                <Badge variant="outline" className="border-yellow-300 text-yellow-800">
                  只读
                </Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Ingestion</CardTitle>
                <Badge variant="secondary">待接入</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4 opacity-70">
              <div className="space-y-2">
                <Label htmlFor="pdf_parser" className="text-xs">
                  PDF 解析器
                </Label>
                <Select defaultValue="mineru" disabled>
                  <SelectTrigger id="pdf_parser" className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="mineru">MinerU</SelectItem>
                    <SelectItem value="llamaparse">LlamaParse</SelectItem>
                    <SelectItem value="unstructured">Unstructured</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="ocr_enable" className="text-xs">
                  启用 OCR
                </Label>
                <Switch id="ocr_enable" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="table_parsing" className="text-xs">
                  表格解析
                </Label>
                <Switch id="table_parsing" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="formula_parsing" className="text-xs">
                  公式抽取
                </Label>
                <Switch id="formula_parsing" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="image_caption" className="text-xs">
                  图片 Caption
                </Label>
                <Switch id="image_caption" defaultChecked disabled />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">TaskGraph</CardTitle>
                <Badge variant="secondary">待接入</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4 opacity-70">
              <div className="flex items-center justify-between">
                <Label htmlFor="evidence_gate" className="text-xs">
                  证据门控
                </Label>
                <Switch id="evidence_gate" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="local_retry" className="text-xs">
                  局部重检
                </Label>
                <Switch id="local_retry" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="citation_verify" className="text-xs">
                  引用校验
                </Label>
                <Switch id="citation_verify" defaultChecked disabled />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="query_progress" className="text-xs">
                  查询进度展示
                </Label>
                <Switch id="query_progress" defaultChecked disabled />
              </div>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}
