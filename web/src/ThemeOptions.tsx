import * as stylex from "@stylexjs/stylex";
import { useState } from "react";
import { Button } from "@tuja/ui/components/button";
import { Heading } from "@tuja/ui/components/heading";
import { Text } from "@tuja/ui/components/text";
import { SegmentedControl } from "@tuja/ui/components/segmented-control";
import { accentThemes, applyAppearance, readAppearance, type Accent, type DisplayMode } from "./appearance";
import "./theme-options.css";

const options: { id: Accent; title: string; subtitle: string; description: string }[] = [
  { id: "ink", title: "墨黑", subtitle: "A / 原版默认", description: "最接近原版。按钮与数据层次清楚，绿色留给成功状态。" },
  { id: "forest", title: "深绿", subtitle: "B / 安静温和", description: "与账本标志呼应，适合更有生活感的界面。" },
  { id: "navy", title: "藏蓝", subtitle: "C / 冷静克制", description: "偏理性的工具感，保留清晰的操作层级。" },
];

export function AppearanceOptions() {
  const [appearance, setAppearance] = useState(readAppearance);
  function update(patch: Partial<typeof appearance>) {
    const value = { ...appearance, ...patch };
    setAppearance(value); applyAppearance(value);
  }
  return <div className="appearance-options">
    <div className="appearance-intro">
      <Heading level={2} look="h4">按钮配色</Heading>
      <Text tone="muted">三种方案使用相同的原版组件。以下为浅色示例，选择后立即应用到本设备。</Text>
    </div>
    <div className="appearance-grid">
      {options.map((option) => <section key={option.id} {...stylex.props(accentThemes[option.id])}><div className="appearance-card" data-option={option.id}>
        <div className="appearance-caption">{option.subtitle}</div>
        <Heading level={3} look="h4">{option.title}</Heading>
        <p className="appearance-description">{option.description}</p>
        <div className="appearance-sample">
          <span className="appearance-caption">示例 · 月度预算</span>
          <div className="appearance-budget"><span>餐饮</span><strong>¥ 5,500</strong></div>
          <div className="appearance-progress"><i /></div>
          <div className="appearance-actions">
            <Button look="primary" onClick={() => update({ accent: option.id })} aria-pressed={appearance.accent === option.id}>
              {appearance.accent === option.id ? `已使用${option.title}` : `使用${option.title}`}
            </Button>
            <Button look="ghost" onClick={() => update({ accent: option.id })}>查看效果</Button>
          </div>
        </div>
      </div></section>)}
    </div>
    <div className="appearance-mode">
      <Heading level={2} look="h4">显示模式</Heading>
      <SegmentedControl aria-label="显示模式" options={[{ value: "system", label: "跟随系统" }, { value: "light", label: "浅色" }, { value: "dark", label: "深色" }]} value={appearance.mode} onChange={(value) => update({ mode: value as DisplayMode })} />
      <Text look="bodySmall" tone="muted">只改变本设备的显示，不修改账本、预算或分类规则。</Text>
    </div>
  </div>;
}
