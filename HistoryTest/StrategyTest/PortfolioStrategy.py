# -*- coding: utf-8 -*-
"""投资组合配置型策略"""
import os

import pandas as pd
import numpy as np
from traits.api import Bool, Enum, Either, List, ListStr, Int, Float, Str, Bool, Dict, Instance, on_trait_change

from QuantStudio.Tools.AuxiliaryFun import searchNameInStrList, getFactorList, genAvailableName, match2Series
from QuantStudio.Tools.FileFun import listDirFile, writeFun2File
from QuantStudio.Tools.MathFun import CartesianProduct
from QuantStudio.Tools.StrategyTestFun import loadCSVFilePortfolioSignal, writePortfolioSignal2CSV
from QuantStudio import __QS_Error__, __QS_Object__
from QuantStudio.HistoryTest.StrategyTest.StrategyTestModule import Strategy, Account

# 信号数据格式: pd.Series(目标权重, index=[ID]) 或者 None(表示无信号, 默认值)

class _WeightAllocation(__QS_Object__):
    """权重分配"""
    ReAllocWeight = Bool(False, label="重配权重", arg_type="Bool", order=0)
    WeightFactor = Enum("等权", label="权重因子", arg_type="SingleOption", order=1)
    GroupFactors = List(label="分类因子", arg_type="MultiOption", order=2, option_range=())
    GroupWeight = Enum("等权", label="类别权重", arg_type="SingleOption", order=3)
    GroupMiss = Enum("忽略","全配", label="类别缺失", arg_type="SingleOption", order=4)
    WeightMiss = Enum("舍弃", "填充均值", label="权重缺失", arg_type="SingleOption", order=5)
    EmptyAlloc = Enum('清空持仓', '保持仓位', '市场组合', label="零股处理", arg_type="SingleOption", order=6)
    def __init__(self, ft=None, sys_args={}, **kwargs):
        self._FT = ft
        return super().__init__(sys_args=sys_args, **kwargs)
    def __QS_initArgs__(self):
        DefaultNumFactorList, DefaultStrFactorList = getFactorList(dict(self._FT.getFactorMetaData(key="DataType")))
        self.add_trait("WeightFactor", Enum(*(["等权"]+DefaultNumFactorList), arg_type="SingleOption", label="权重因子", order=1))
        self.add_trait("GroupFactor", Enum(*self._FT.FactorNames, arg_type="MultiOption", label="类别因子", order=2, option_range=("等权", )+tuple(DefaultNumFactorList)))
        self.add_trait("GroupWeight", Enum(*(["等权"]+DefaultNumFactorList), arg_type="SingleOption", label="类别权重", order=3))

# 投资组合策略
class PortfolioStrategy(Strategy):
    SigalDelay = Int(0, label="信号滞后期", arg_type="Integer", order=0)
    SigalValidity = Int(1, label="信号有效期", arg_type="Integer", order=1)
    LongSigalDTs = List(label="多头信号时点", arg_type="DateTimeList", order=2)
    ShortSigalDTs = List(label="空头信号时点", arg_type="DateTimeList", order=3)
    LongWeightAlloction = Instance(_WeightAllocation, label="多头权重配置", arg_type="ArgObject", order=4)
    ShortWeightAlloction = Instance(_WeightAllocation, label="空头权重配置", arg_type="ArgObject", order=5)
    LongAccount = Instance(Account, label="多头账户", arg_type="ArgObject", order=6)
    ShortAccount = Instance(Account, label="空头账户", arg_type="ArgObject", order=7)
    TradeTarget = Enum("锁定买卖金额", "锁定目标权重", "锁定目标金额", label="交易目标", arg_type="SingleOption", order=8)
    def __init__(self, name, factor_table=None, sys_args={}, **kwargs):
        self._FT = factor_table# 因子表
        super().__init__(name, sys_args=sys_args, **kwargs)
        self._AllLongSignals = {}# 存储所有生成的多头信号, {时点:信号}
        self._AllShortSignals = {}# 存储所有生成的空头信号, {时点:信号}
        return
    def __QS_initArgs__(self):
        super().__QS_initArgs__()
        self.LongWeightAlloction = _WeightAllocation(ft=self._FT)
        self.ShortWeightAlloction = _WeightAllocation(ft=self._FT)
    @on_trait_change("LongAccount")
    def _on_LongAccount_changed(self, obj, name, old, new):
        if self.LongAccount and (self.LongAccount not in self.Accounts): self.Accounts.append(self.LongAccount)
        elif self.LongAccount is None: self.Accounts.remove(old)
    @on_trait_change("ShortAccount")
    def _on_ShortAccount_changed(self, obj, name, old, new):
        if self.ShortAccount and (self.ShortAccount not in self.Accounts): self.Accounts.append(self.ShortAccount)
        elif self.ShortAccount is None: self.Accounts.remove(old)
    def __QS_start__(self, mdl, dts=None, dates=None, times=None):
        self._AllLongSignals = {}
        self._AllShortSignals = {}
        self._LongTradeTarget = None# 锁定的多头交易目标
        self._ShortTradeTarget = None# 锁定的空头交易目标
        self._LongSignalExcutePeriod = 0# 多头信号已经执行的期数
        self._ShortSignalExcutePeriod = 0# 空头信号已经执行的期数
        # 初始化信号滞后发生的控制变量
        self._TempData = {}
        self._TempData['StoredSignal'] = []# 暂存的信号，用于滞后发出信号
        self._TempData['LagNum'] = []# 当前日距离信号生成日的日期数
        return super().__QS_start__(mdl=mdl, dts=dts, dates=dates, times=times)
    def __QS_end__(self):
        self._LongTradeTarget, self._ShortTradeTarget = None, None
        self._TempData = {}
        return 0
    # 生成多头信号, 用户实现
    def genLongSignal(self, idt, trading_record):
        return None
    # 生成空头信号, 用户实现
    def genShortSignal(self, idt, trading_record):
        return None
    def genSignal(self, idt, trading_record):
        LongSignal, ShortSignal = None, None
        if (self.LongAccount is not None) and ((not self.LongSigalDTs) or (idt in self.LongSigalDTs)):
            LongSignal = self.genLongSignal(idt, trading_record)
        if (self.ShortAccount is not None) and ((not self.ShortSigalDTs) or (idt in self.ShortSigalDTs)):
            ShortSignal = self.genShortSignal(idt, trading_record)
        return (LongSignal, ShortSignal)
    def trade(self, idt, trading_record, signal):
        LongSignal, ShortSignal = signal
        if LongSignal is not None:
            if self.LongWeightAlloction.ReAllocWeight:
                LongSignal = self._allocateWeight(idt, LongSignal.index.tolist(), self.LongAccount.IDs, self.LongWeightAlloction)
            self._AllLongSignals[idt] = LongSignal
        if ShortSignal is not None:
            if self.ShortWeightAlloction.ReAllocWeight:
                ShortSignal = self._allocateWeight(idt, ShortSignal.index.tolist(), self.ShortAccount.IDs, self.ShortWeightAlloction)
            self._AllShortSignals[idt] = ShortSignal
        LongSignal, ShortSignal = self._bufferSignal(LongSignal, ShortSignal)
        self._processLongSignal(idt, trading_record, LongSignal)
        self._processShortSignal(idt, trading_record, ShortSignal)
        return 0
    def _processLongSignal(self, idt, trading_record, long_signal):
        if self.LongAccount is None: return 0
        AccountValue = self.LongAccount.AccountValue
        pHolding = self.LongAccount.PositionAmount
        pHolding = pHolding[pHolding>0]
        pHolding = pHolding / AccountValue
        if long_signal is not None:# 有新的信号, 形成新的交易目标
            if self.TradeTarget=="锁定买卖金额":
                pSignalHolding, long_signal = match2Series(pHolding, long_signal, fillna=0.0)
                self._LongTradeTarget = (long_signal - pSignalHolding) * AccountValue
            elif self.TradeTarget=="锁定目标权重":
                self._LongTradeTarget = long_signal
            elif self.TradeTarget=="锁定目标金额":
                self._LongTradeTarget = long_signal*AccountValue
            self._LongSignalExcutePeriod = 0
        elif self._LongTradeTarget is not None:# 没有新的信号, 根据交易记录调整交易目标
            self._LongSignalExcutePeriod += 1
            if self._LongSignalExcutePeriod>=self.SigalValidity:
                self._LongTradeTarget = None
                self._LongSignalExcutePeriod = 0
            else:
                iTradingRecord = trading_record[self.LongAccount.Name]
                iTradingRecord = iTradingRecord.set_index(["ID"]).ix[self._LongTradeTarget.index]
                if self.TradeTarget=="锁定买卖金额":
                    TargetChanged = iTradingRecord["数量"] * iTradingRecord["价格"]
                    TargetChanged[pd.isnull(TargetChanged)] = 0.0
                    self._LongTradeTarget = self._LongTradeTarget - TargetChanged
        # 根据交易目标下订单
        if self._LongTradeTarget is not None:
            if self.TradeTarget=="锁定买卖金额":
                Orders = self._LongTradeTarget
            elif self.TradeTarget=="锁定目标权重":
                pHolding, LongTradeTarget = match2Series(pHolding, self._LongTradeTarget, fillna=0.0)
                Orders = (LongTradeTarget - pHolding) * AccountValue
            elif self.TradeTarget=="锁定目标金额":
                pHolding, LongTradeTarget = match2Series(pHolding, self._LongTradeTarget, fillna=0.0)
                Orders = LongTradeTarget - pHolding*AccountValue
            LastPrice = self.LongAccount.LastPrice
            Orders = Orders / LastPrice[Orders.index]
            Orders = Orders[pd.notnull(Orders) & (Orders!=0)]
            if Orders.shape[0]==0: return 0
            Orders = pd.DataFrame(Orders).reset_index()
            Orders.columns = ["ID", "数量"]
            Orders["目标价"] = np.nan
            self.LongAccount.order(combined_order=Orders)
        return 0
    def _processShortSignal(self, idt, trading_record, short_signal):
        return 0# TODO
    # 配置权重
    def _allocateWeight(self, idt, ids, original_ids, args):
        nID = len(ids)
        if nID==0:# 零股处理
            if args.EmptyAlloc=='保持仓位': return None
            elif args.EmptyAlloc=='清空持仓': return pd.Series([])
            elif args.EmptyAlloc=='市场组合': ids=original_ids
        if not args.GroupFactors:# 没有类别因子
            if args.WeightFactor=='等权': NewSignal = pd.Series(1/nID, index=ids)
            else:
                WeightData = self._FT.readData(factor_names=[args.WeightFactor], dts=[idt], ids=ids).iloc[0,0,:]
                if args.WeightMiss=='舍弃': WeightData = WeightData[pd.notnull(WeightData)]
                else: WeightData[pd.notnull(WeightData)] = WeightData.mean()
                WeightData = WeightData / WeightData.sum()
                NewSignal = WeightData
        else:
            GroupData = self._FT.readData(factor_names=args.GroupFactors, dts=[idt], ids=original_ids).iloc[:,0,:]
            GroupData[pd.isnull(GroupData)] = np.nan
            AllGroups = [GroupData[iGroup].unique().tolist() for iGroup in args.GroupFactors]
            AllGroups = CartesianProduct(AllGroups)
            nGroup = len(AllGroups)
            if args.GroupWeight=='等权': GroupWeight = pd.Series(np.ones(nGroup)/nGroup, dtype='float')
            else:
                GroupWeight = pd.Series(index=np.arange(nGroup), dtype='float')
                GroupWeightData = self._FT.readData(factor_names=[args.GroupWeight], dts=[idt], ids=original_ids).iloc[0,0,:]
                for i, iGroup in enumerate(AllGroups):
                    if pd.notnull(iGroup[0]): iMask = (GroupData[args.GroupFactors[0]]==iGroup[0])
                    else: iMask = pd.isnull(GroupData[args.GroupFactors[0]])
                    for j, jSubGroup in enumerate(iGroup[1:]):
                        if pd.notnull(jSubGroup): iMask = (iMask & (GroupData[args.GroupFactors[j+1]]==jSubGroup))
                        else: iMask = (iMask & pd.isnull(GroupData[args.GroupFactors[j+1]]))
                    GroupWeight.iloc[i] = GroupWeightData[iMask].sum()
                GroupWeight[pd.isnull(GroupWeight)] = 0
                GroupTotalWeight = GroupWeight.sum()
                if GroupTotalWeight!=0: GroupWeight = GroupWeight/GroupTotalWeight
            if args.WeightFactor=='等权': WeightData = pd.Series(1.0, index=original_ids)
            else: WeightData = self._FT.readData(factor_names=[args.WeightFactor], dts=[idt], ids=original_ids).iloc[0,0,:]
            SelectedGroupData = GroupData.loc[ids]
            NewSignal = pd.Series()
            for i, iGroup in enumerate(AllGroups):
                if pd.notnull(iGroup[0]): iMask = (SelectedGroupData[args.GroupFactors[0]]==iGroup[0])
                else: iMask = pd.isnull(SelectedGroupData[args.GroupFactors[0]])
                for j, jSubGroup in enumerate(iGroup[1:]):
                    if pd.notnull(jSubGroup): iMask = (iMask & (SelectedGroupData[args.GroupFactors[j+1]]==jSubGroup))
                    else: iMask = (iMask & pd.isnull(SelectedGroupData[args.GroupFactors[j+1]]))
                iIDs = SelectedGroupData[iMask].index.tolist()
                if (iIDs==[]) and (args.GroupMiss=='全配'):
                    if pd.notnull(iGroup[0]): iMask = (GroupData[args.GroupFactors[0]]==iGroup[0])
                    else: iMask = pd.isnull(GroupData[args.GroupFactors[0]])
                    for k, kSubClass in enumerate(iGroup[1:]):
                        if pd.notnull(kSubClass): iMask = (iMask & (GroupData[args.GroupFactors[k+1]]==kSubClass))
                        else: iMask = (iMask & pd.isnull(GroupData[args.GroupFactors[k+1]]))
                    iIDs = GroupData[iMask].index.tolist()
                elif (iIDs==[]) and (args.GroupMiss=='忽略'): continue
                iSignal = WeightData.loc[iIDs]
                iSignalWeight = iSignal.sum()
                if iSignalWeight!=0: iSignal = iSignal / iSignalWeight * GroupWeight.iloc[i]
                else: iSignal = iSignal*0.0
                if args.WeightMiss=='填充均值': iSignal[pd.isnull(iSignal)] = iSignal.mean()
                NewSignal = NewSignal.append(iSignal[pd.notnull(iSignal) & (iSignal!=0)])
            NewSignal = NewSignal / NewSignal.sum()
        return NewSignal
    # 将信号缓存，并弹出滞后期到期的信号
    def _bufferSignal(self, long_signal, short_signal):
        if (long_signal is not None) or (short_signal is not None):
            self._TempData['StoredSignal'].append((long_signal, short_signal))
            self._TempData['LagNum'].append(-1)
        for i,iLagNum in enumerate(self._TempData['LagNum']):
            self._TempData['LagNum'][i] = iLagNum+1
        LongSignal, ShortSignal = None, None
        while self._TempData['StoredSignal']!=[]:
            if self._TempData['LagNum'][0]>=self.SigalDelay:
                LongSignal, ShortSignal = self._TempData['StoredSignal'].pop(0)
                self._TempData['LagNum'].pop(0)
            else:
                break
        return (LongSignal, ShortSignal)

# 分层筛选投资组合策略
class _TurnoverBuffer(__QS_Object__):
    """换手缓冲"""
    isBuffer = Bool(False, label="是否缓冲", arg_type="Bool", order=0)
    FilterUpBuffer = Float(0.1, label="筛选上限缓冲区", arg_type="Double", order=1)
    FilterDownBuffer = Float(0.1, label="筛选下限缓冲区", arg_type="Double", order=2)
    FilterNumBuffer = Int(0, label="筛选数目缓冲区", arg_type="Integer", order=3)

class _Filter(__QS_Object__):
    """筛选"""
    SignalType = Enum("多头信号", "空头信号", label="信号类型", arg_type="SingleOption", order=0)
    IDFilter = Str(arg_type="IDFilter", label="筛选条件", order=1)
    TargetFactor = Enum(None, label="目标因子", arg_type="SingleOption", order=2)
    FactorOrder = Enum("降序", "升序", label="排序方向", arg_type="SingleOption", order=3)
    FilterType = Enum("定量", "定比", "定量&定比", label="筛选方式", arg_type="SingleOption", order=4)
    FilterNum = Int(30, label="筛选数目", arg_type="Integer", order=5)
    GroupFactors = List(label="分类因子", arg_type="MultiOption", order=6, option_range=())
    TurnoverBuffer = Instance(_TurnoverBuffer, arg_type="ArgObject", label="换手缓冲", order=7)
    def __init__(self, ft=None, sys_args={}, **kwargs):
        self._FT = ft
        return super().__init__(sys_args=sys_args, **kwargs)
    def __QS_initArgs__(self):
        self.TurnoverBuffer = _TurnoverBuffer()
        DefaultNumFactorList, DefaultStrFactorList = getFactorList(dict(self._FT.getFactorMetaData(key="DataType")))
        self.add_trait("TargetFactor", Enum(*DefaultNumFactorList, label="目标因子", arg_type="SingleOption", order=2))
        self.add_trait("GroupFactors", List(arg_type="MultiOption", label="类别因子", order=6, option_range=tuple(self._FT.FactorNames)))
    @on_trait_change("FilterType")
    def _on_FilterType_changed(self, obj, name, old, new):
        if new=='定量':
            if "FilterUpLimit" in self.ArgNames:
                self.remove_trait("FilterUpLimit")
                self.remove_trait("FilterDownLimit")
            if "FilterNum" not in self.ArgNames:
                self.add_trait("FilterNum", Int(30, label="筛选数目", arg_type="Integer", order=5))
        elif new=='定比':
            if "FilterNum" in self.ArgNames:
                self.remove_trait("FilterNum")
            if "FilterUpLimit" not in self.ArgNames:
                self.add_trait("FilterUpLimit", Float(0.1, label="筛选上限", arg_type="Double", order=5.1))
                self.add_trait("FilterDownLimit", Float(0.0, label="筛选下限", arg_type="Double", order=5.2))
        else:
            if "FilterNum" not in self.ArgNames:
                self.add_trait("FilterNum", Int(30, label="筛选数目", arg_type="Integer", order=5))
            if "FilterUpLimit" not in self.ArgNames:
                self.add_trait("FilterUpLimit", Float(0.1, label="筛选上限", arg_type="Double", order=5.1))
                self.add_trait("FilterDownLimit", Float(0.0, label="筛选下限", arg_type="Double", order=5.2))
class HierarchicalFiltrationStrategy(PortfolioStrategy):
    FilterLevel = Int(1, label="筛选层数", arg_type="Integer", order=9)
    def __QS_initArgs__(self):
        super().__QS_initArgs__()
        self.add_trait("Level0", Instance(_Filter, label="第0层", arg_type="ArgObject", order=10))
        self.Level0 = _Filter(ft=self._FT)
        self.LongWeightAlloction.ReAllocWeight = True
        self.ShortWeightAlloction.ReAllocWeight = True
    @on_trait_change("FilterLevel")
    def on_FilterLevel_changed(self, obj, name, old, new):
        ArgNames = self.ArgNames
        if new>old:# 增加了筛选层数
            for i in range(max(0, old), max(0, new)):
                self.add_trait("Level"+str(i), Instance(_Filter, label="第"+str(i)+"层", arg_type="ArgObject", order=10+i))
                setattr(self, "Level"+str(i), _Filter(ft=self._FT))
        elif new<old:# 减少了筛选层数
            for i in range(max(0, old)-1, max(0, new)-1, -1):
                self.remove_trait("Level"+str(i))
    def _filtrateID(self, idt, ids, args):
        FactorData = self._FT.readData(dts=[idt], ids=ids, factor_names=[args.TargetFactor]).iloc[0,0,:]
        FactorData = FactorData[pd.notnull(FactorData)]
        if args.FactorOrder=='降序': FactorData = -FactorData
        FactorData = FactorData.sort_values(ascending=True)
        if args.FilterType=='定比':
            UpLimit = FactorData.quantile(args.FilterUpLimit)
            DownLimit = FactorData.quantile(args.FilterDownLimit)
            NewIDs = FactorData[(FactorData>=DownLimit) & (FactorData<=UpLimit)].index.tolist()
        elif args.FilterType=='定量':
            NewIDs = FactorData.iloc[:args.FilterNum].index.tolist()
        elif args.FilterType=='定量&定比':
            UpLimit = FactorData.quantile(args.FilterUpLimit)
            DownLimit = FactorData.quantile(args.FilterDownLimit)
            NewIDs = FactorData.iloc[:args.FilterNum].index.intersection(FactorData[(FactorData>=DownLimit) & (FactorData<=UpLimit)].index).tolist()
        if not args.TurnoverBuffer.isBuffer: return NewIDs
        SignalIDs = set(NewIDs)
        nSignalID = len(SignalIDs)
        if args.SignalType=="多头信号":
            if self._AllLongSignals=={}: LastIDs = set()
            else: LastIDs = set(self._AllLongSignals[max(self._AllLongSignals)].index)
        else:
            if self._AllShortSignals=={}: LastIDs = set()
            else: LastIDs = set(self._AllShortSignals[max(self._AllShortSignals)].index)
        if args.FilterType=='定比':
            UpLimit = FactorData.quantile(min(1.0, args.FilterUpLimit+args.TurnoverBuffer.FilterUpBuffer))
            DownLimit = FactorData.quantile(max(0.0, args.FilterDownLimit-args.TurnoverBuffer.FilterDownBuffer))
            NewIDs = LastIDs.intersection(FactorData[(FactorData>=DownLimit) & (FactorData<=UpLimit)].index)
        elif args.FilterType=='定量':
            NewIDs = LastIDs.intersection(FactorData.iloc[:args.FilterNum+args.TurnoverBuffer.FilterNumBuffer].index)
        elif args.FilterType=='定量&定比':
            UpLimit = FactorData.quantile(min(1.0, args.FilterUpLimit+args.TurnoverBuffer.FilterUpBuffer))
            DownLimit = FactorData.quantile(max(0.0, args.FilterDownLimit-args.TurnoverBuffer.FilterDownBuffer))
            NewIDs = LastIDs.intersection(FactorData.iloc[:args.FilterNum+args.TurnoverBuffer.FilterNumBuffer].index).intersection(FactorData[(FactorData>=DownLimit) & (FactorData<=UpLimit)].index)
        if len(NewIDs)>=nSignalID:# 当前持有的股票已经满足要求
            FactorData = FactorData[list(NewIDs)].copy()
            FactorData.sort_values(inplace=True, ascending=True)
            return FactorData.iloc[:nSignalID].index.tolist()
        SignalIDs = list(SignalIDs.difference(NewIDs))
        FactorData = FactorData[SignalIDs].copy()
        FactorData.sort_values(inplace=True, ascending=True)
        return list(NewIDs)+FactorData.iloc[:(nSignalID-len(NewIDs))].index.tolist()
    def _genSignalIDs(self, idt, original_ids, signal_type):
        IDs = original_ids
        for i in range(self.FilterLevel):
            iArgs = self["第"+str(i)+"层"]
            if iArgs.SignalType!=signal_type: continue
            if iArgs.IDFilter:
                iIDs = self._FT.getFilteredID(idt, id_filter_str=iArgs.IDFilter)
                IDs = sorted(set(iIDs).intersection(set(IDs)))
            if iArgs.GroupFactors!=[]:
                GroupData = self._FT.readData(dts=[idt], ids=IDs, factor_names=iArgs.GroupFactors).iloc[:,0,:]
                if GroupData.shape[0]>0: GroupData[pd.isnull(GroupData)] = np.nan
                AllGroups = [GroupData[iGroup].unique().tolist() for iGroup in iArgs.GroupFactors]
                AllGroups = CartesianProduct(AllGroups)
                IDs = []
                for jGroup in AllGroups:
                    jMask = pd.Series(True, index=GroupData.index)
                    for k, kSubGroup in enumerate(jGroup):
                        if pd.notnull(kSubGroup): jMask = (jMask & (GroupData[iArgs.GroupFactors[k]]==kSubGroup))
                        else: jMask = (jMask & pd.isnull(GroupData[iArgs.GroupFactors[k]]))
                    jIDs = self._filtrateID(idt, GroupData[jMask].index.tolist(), iArgs)
                    IDs += jIDs
            else:
                IDs = self._filtrateID(idt, IDs, iArgs)
        return IDs
    def genLongSignal(self, idt, trading_record):
        if self.LongAccount is None: return None
        OriginalIDs = self.LongAccount.IDs
        IDs = self._genSignalIDs(idt, OriginalIDs, '多头信号')
        return pd.Series(1/len(IDs), index=IDs)
    def genShortSignal(self, idt, trading_record):
        if self.ShortAccount is None: return None
        OriginalIDs = self.ShortAccount.IDs
        IDs = self._genSignalIDs(idt, OriginalIDs, '空头信号')
        return pd.Series(1/len(IDs), index=IDs)