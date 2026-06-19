import React, { useState, useEffect } from 'react';
import { useStore } from '@nanostores/react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { RecommendedPanel } from './recommended-panel';
import { $activeUnits, $placements, addUnitToRandomHex, placeUnit } from '@/store/boardStore';
import { postSuggest, getUnitsMap } from '@/lib/api';
import type { SuggestResult, TFTUnit } from '@/types';

const BOARD_ROWS = 4;
const BOARD_COLS = 7;

export const RecommendedPanelTabs: React.FC = () => {
  const activeUnits = useStore($activeUnits);
  const placements = useStore($placements);
  const [result, setResult] = useState<SuggestResult | null>(null);
  const [unitsMap, setUnitsMap] = useState<Map<string, TFTUnit>>(new Map());
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('comps');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Fetch suggestions whenever the board changes, debounced
  useEffect(() => {
    if (!activeUnits || activeUnits.length === 0) {
      setResult(null);
      return;
    }

    const fetchData = async () => {
      setLoading(true);
      try {
        const [suggestRes, mapRes] = await Promise.all([
          postSuggest({ units: activeUnits }),
          getUnitsMap(),
        ]);
        setResult(suggestRes);
        setUnitsMap(mapRes);
      } catch (err) {
        console.error('Failed to fetch suggestions:', err);
        setResult(null);
      } finally {
        setLoading(false);
      }
    };

    const handler = setTimeout(fetchData, 500);
    return () => clearTimeout(handler);
  }, [activeUnits]);

  const hasComps = (result?.suggested_comps?.length ?? 0) > 0;
  const hasUnitsToAdd = (result?.additions?.length ?? 0) > 0;
  const hasUnitsToMutate = (result?.mutations?.length ?? 0) > 0;

  // Auto-switch away from a tab that becomes empty
  useEffect(() => {
    if (!mounted) return;
    if (activeTab === 'comps' && !hasComps) {
      if (hasUnitsToAdd) setActiveTab('additions');
      else if (hasUnitsToMutate) setActiveTab('mutations');
    } else if (activeTab === 'additions' && !hasUnitsToAdd) {
      if (hasComps) setActiveTab('comps');
      else if (hasUnitsToMutate) setActiveTab('mutations');
    } else if (activeTab === 'mutations' && !hasUnitsToMutate) {
      if (hasComps) setActiveTab('comps');
      else if (hasUnitsToAdd) setActiveTab('additions');
    }
  }, [hasComps, hasUnitsToAdd, hasUnitsToMutate]);

  // Add a unit to a random empty hex
  const handleAddUnit = (unitName: string) => {
    const unit = unitsMap.get(unitName.toLowerCase());
    if (!unit) return;
    addUnitToRandomHex(unit, BOARD_ROWS, BOARD_COLS);
  };

  // Swap a unit on the board with a suggested replacement
  const handleSwapUnit = (unitOut: string, unitIn: string) => {
    const inUnit = unitsMap.get(unitIn.toLowerCase());
    if (!inUnit) return;

    // Find the hex key of the unit being swapped out
    const key = Object.entries(placements).find(
      ([, u]) => u.name.toLowerCase() === unitOut.toLowerCase()
    )?.[0];

    if (!key) {
      // Unit to swap out isn't on the board — just add the new one
      addUnitToRandomHex(inUnit, BOARD_ROWS, BOARD_COLS);
      return;
    }

    placeUnit(key, inUnit);
  };

  return (
    <Tabs
      value={activeTab}
      onValueChange={setActiveTab}
      className="flex h-full w-full min-h-0 flex-col"
    >
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger value="comps" disabled={mounted ? !hasComps : false}>
          Comps
        </TabsTrigger>
        <TabsTrigger value="additions" disabled={mounted ? !hasUnitsToAdd : false}>
          Add
        </TabsTrigger>
        <TabsTrigger value="mutations" disabled={mounted ? !hasUnitsToMutate : false}>
          Swap
        </TabsTrigger>
      </TabsList>

      <TabsContent value="comps" className="flex-1 overflow-y-auto p-4">
        <RecommendedPanel
          mode="comps"
          externalResult={result}
          externalUnitsMap={unitsMap}
          isLoading={loading}
          onAddUnit={handleAddUnit}
          onSwapUnit={handleSwapUnit}
        />
      </TabsContent>

      <TabsContent value="additions" className="flex-1 overflow-y-auto p-4">
        <RecommendedPanel
          mode="additions"
          externalResult={result}
          externalUnitsMap={unitsMap}
          isLoading={loading}
          onAddUnit={handleAddUnit}
          onSwapUnit={handleSwapUnit}
        />
      </TabsContent>

      <TabsContent value="mutations" className="flex-1 overflow-y-auto p-4">
        <RecommendedPanel
          mode="mutations"
          externalResult={result}
          externalUnitsMap={unitsMap}
          isLoading={loading}
          onAddUnit={handleAddUnit}
          onSwapUnit={handleSwapUnit}
        />
      </TabsContent>
    </Tabs>
  );
};