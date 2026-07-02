import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import React from "react"
import { ChampionDisplay } from '@/components/builder/champion-display'
import { ItemsPanel } from '@/components/builder/items-panel'
import type { TFTUnit } from "@/types"

interface BottomTabsProps {
  defaultValue?: string
  className?: string
  champions?: TFTUnit[]
}

/** Bottom panel tabs: the champion pool and per-unit item analytics. */
export function BottomTabs({ defaultValue, className, champions }: BottomTabsProps) {
  return (
    <Tabs defaultValue={defaultValue || "overview"} className={className}>
      <TabsList className="w-full justify-start rounded-none border-b bg-transparent p-0">
        <TabsTrigger value="overview">Champions</TabsTrigger>
        <TabsTrigger value="analytics">Detailed Analytics</TabsTrigger>
      </TabsList>
      <TabsContent value="overview" className="mt-0 outline-none p-0">
        {champions && <ChampionDisplay units={champions} />}
      </TabsContent>
      <TabsContent value="analytics" className="mt-0 outline-none">
        <ItemsPanel />
      </TabsContent>
    </Tabs>
  )
}
