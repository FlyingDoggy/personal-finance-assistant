"""Local, reproducible interpretation of immutable finance evidence (spec v0.3).

Usage: python3 scripts/process_finance.py --config data/processing_config.json
Amounts in SQLite are integer CNY cents. Unknown balances are NULL, never zero.
No network calls. This is a processing checkpoint, not a bank connection.
"""
import argparse
import calendar
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

VERSION = '0.3.2'
EXPENSES = dict(zip(range(5001, 5017), ['餐饮','日用居家','住房','交通','服饰鞋包','个人护理','健康与宠物','文娱学习与运动','数码与家电','数字服务','人情赠与','利息','手续费','税费与个人社保','其他已确认支出','未分类支出']))
COA = {1001:'现金及银行存款',1002:'内部转账在途',1003:'信用支付溢缴款',1004:'待收报销款',1005:'其他应收款',1006:'可退押金',1007:'公积金',1008:'投资成本',1009:'投资估值调整',2001:'信用支付结算应付款',2002:'融资分期借款',2003:'房贷本金',2004:'其他借款本金',2005:'其他应付款',3001:'纳入范围期初净额',3002:'纳入范围变更',4001:'劳动收入',4002:'已实现投资损益',4003:'未实现投资估值损益',4004:'分红及利息收入',4005:'其他业务收入',**EXPENSES}
LABELS = {'cmb_debit':'招行借记卡','citic_debit':'中信借记卡','bob_debit':'北京银行借记卡','cmb_credit':'招行信用卡','citic_credit':'中信信用卡','spdb_credit':'浦发信用卡','alipay':'花呗','jd_baitiao':'京东白条'}
PATTERNS = [
 (5001, r'便利蜂|快客便利|肉火烧|烤鹅|快餐店|SKY\+LOUNGE|魔盒CITYBOX|下酒凤凰汇'),
 (5004, r'星星充电'),
 (5001, r'盒马|果蔬好|山姆|沃尔玛|超市|便利店|便利商店|7[－-]11|7[－-]?ELEVEN|柒一拾壹|罗森|京东到家|朴朴|叮咚|钱粮市集|TESCO|SAINSBURY.*(?:STORES|S/MKTS)|CO-OP GROUP|家家悦'),
 (5002, r'MUJI|无印良品|宜家|IKEA|鞋柜|工学椅|办公椅|主机托架|置物架|增高架|床笠|床套|家纺|四件套'),
 (5007, r'狗狗|猫咪|狗粮|犬粮|猫粮|遛狗|驱虫药|宠爱宝盒|PETCARE|HONEYCARE|PAWZ|小佩|鱼跃|静脉曲张|医药连锁'),
 (5009, r'烧水壶|电烤箱|充电器|充电线|数据线|电源适配器|GOOGLE\s*PIXEL|手机壳|手机.*(?:膜|底座)|键盘|鼠标|妙控板|耳机|麦克风|数码|路由器|USB|TYPE[ -]?C|插座|插排|插线板|电风扇|智能音箱|摩米士|绿联|顺电|魔极客|APPLE\.COM/UK|徕芬'),
 (5004, r'汽车护理|汽车用品|行车记录仪|驾照|专车付费|汽车.*(?:头枕|钥匙)|沃尔沃|ETC助手|Flight Pass|贵宾厅|团油|PETROL|SERVICE STATION|S/STN|TFL\s*TRAVEL|TFLTRAVEL|PAY-DARTFORD|车场|车联网|静态交通|首兴智行|哈啰|蔚来汽车|理想汽车|极氪汽车'),
 (5007, r'宠物|动物医院|泰格|暖宠|阿里健康|大药房|药店|医院|体检|医疗'),
 (5005, r'蕉内|马登工装|PAIR.?PAIR.?FULLSOCKS|PANE(?:[（(]|$)|凉鞋|拖鞋|徒步鞋|运动鞋|T[ -]?SHIRT|T恤|短袖|背心|耳钉|耳夹|发带|袜子|始祖鸟|ARC.TERYX'),
 (5010, r'CURSOR|NETFLIX|GOOGLE\s*(?:ONE|G1|PLAY|CLOUD)|GOOGLE\s*\*|FIGMA|APPLE\.COM/BILL|APP\s?STORE|APPLEMUSIC|苹果服务|迅雷|飞书|阿里云|BANDWAGON|BWH|OPENAI|CHATGPT|ADOBE|ICLOUD|MICROSOFT\*|微软.*订阅|GITHUB|CLOUDFLARE|火山引擎|月之暗面|中国联合网络通信|北京联通|ESIM|电话卡|上网卡|哔哩哔哩'),
 (5008, r'骑行台|骑行垫|游戏充值|元宝充值|ASMODEE|ILOVETOY|BARBICAN|HISTORICROYALPALACES'),
 (5004, r'嘀嘀|滴滴|打车|UBER|铁路|12306|轨道交通|地铁|摩拜|骑行|通行宝|高速|速通|停车|加油|壳牌|SHELL|石油|充电服务|充电站|新能源|一嗨|租车|SIXT|国航|航空|AIRWAYS|AIRLINE|TRAIN|TRANSPORT|高德|特来电'),
 (5003, r'电力|电费|燃气|水费|物业|供暖|酒店|宾馆|HOTEL|MARRIOTT|ALOFT|MOXY|HOLIDAY INN'),
 (5001, r'拉扎斯|饿了么|餐饮|餐室|餐厅|小食|饭店|酒楼|烤肉|烧肉|烧烤|烤串|烧鹅|火锅|咖啡|COFFEE|CAFE|RESTAURANT|酒馆|鲜啤|小厨|麦当劳|MCDONALD|肯德基|必胜客|紫光园|匠牛|奈雪|瑞幸|茶百道|喜茶|星巴克|馄饨|饺子|面馆|寿司|饼|披萨|羊腿|小龙虾|茶楼|乳鸽|食鸡|打边炉|CHOCOLATE|PUDDING|BARANDGRILL|HOPSANDCHOPS|水煮鱼|极蟹|食品|饮品|饮食|面包|奶酪|酸奶|食用|果蔬|水果|百果园|正大优鲜|农夫山泉|鸭脖|炸鸡|扒鸡|酒家|面庄|虾庄|羊肉串|牛肉|牛蛙|鸡蛋|鲜奶|糕点|肘子|烟酒|制茶|微醺|北平机器|野人先生|抹茶未来|曹祥泰|HUNGER DOG|TastyCongeeNoodle|GREGGS|SUBWAY|BYRON|FARMSHOP|CIDER|FARMER.SDOGPUB|WILLIAM GRANT|MACALLAN|M Stand|Paper Stone|CHOC-A-LOT|FOODBOWL|海珍鲜|艾格吃饱|三号椰|金拱门|宝矿力'),
 (5002, r'垃圾桶|储物盒|收纳筐|收纳箱|收纳盒|理线盒|理线槽|束线器|纸巾|抽纸|湿巾|洗手液|毛巾|浴巾|名创优品|KKV|江吉洗衣'),
 (5005, r'优衣库|UNIQLO|LULULEMON|露露乐蒙|拉尔夫劳伦|BALENCIAGA|SAMSONITE|凯乐石|KAILAS|耐克|NIKE|阿迪|ADIDAS|PUMA|彪马|安踏|鞋|服饰|服装|服装店'),
 (5006, r'理发|美发|美甲|个人护理|屈臣氏|发型|美容|发胶|发蜡|发泥|修脚|按摩椅|SKNFED|BOOTS\d+'),
 (5008, r'影院|电影|书店|门票|运动|体育|迪卡侬|健身|晨电|泡泡玛特|博物馆|图书'),
 (5009, r'数码|电器|苹果电子|内存卡|存储|网线|私有云|HIKVIS|大疆|戴森|石头启迪|电子产品'),
 (5013, r'出入境证件费'),
 (5015, r'顺丰速运|速递物流|闪送订单|物流运费|交通罚没'),
 (5004, r'12123工本费|12123.*考试费'),
 (5015, r'Cashback|返现金'),
 (5001, r'烤.{0,2}鱼|烤鸭|烧鸟|白斩鸡|烫牛肉|鸽园臻宴|家和四季三食|野鹅微醺|沃谷农业|CurdleHillFarm|THATCHERS|THERAILWAYINN|YE OLDE TRIP TO JERUSA|大名家'),
 (5008, r'户外装备|影城|图书|挂件|包挂|PETERRABBIT|Gift Shop|陀思妥|卡拉马佐夫|上海译文|出版社'),
]

def cents(value):
    return int((Decimal(str(value).replace(',', '')) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def rounded(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def classify_category(text):
    if re.search(r'宠物超市|宠物便利',text,re.I):return 5007,'宠物用品场景优先于超市字样'
    if re.search(r'保洁|家政服务',text,re.I):return 5002,'明确居家服务用途'
    for code, pattern in PATTERNS:
        if re.search(pattern, text, re.I):
            return code, '商户/商品语义规则'
    return 5016, '已确认消费；用途不足，保留未分类'

def classify_purchase(merchant, item='', source_category=''):
    # Specific goods are stronger than the store's usual business. User's
    # named-store convention is the default when goods are not identified.
    if item:
        code,reason=classify_category(item)
        if code!=5016:return code,'商品说明语义：'+reason
    if re.search(r'宠物超市|宠物便利',merchant,re.I):return 5007,'宠物用品场景优先于超市字样'
    if re.search(r'盒马|果蔬好|山姆|沃尔玛|超市|便利|7[－-]11|7[－-]?ELEVEN|柒一拾壹|罗森|京东到家|朴朴|叮咚',merchant,re.I):return 5001,'用户口径：食品超市/便利店计餐饮'
    if re.search(r'MUJI|无印良品|宜家|IKEA',merchant,re.I):return 5002,'用户口径：MUJI/宜家计日用居家'
    code,reason=classify_category(merchant)
    if code!=5016:return code,reason
    # Narrow source categories supplement opaque descriptions. Broad platform
    # labels such as 日用百货、生活服务、充值缴费 are not treated as proof.
    narrow={'餐饮美食':5001,'服饰装扮':5005,'宠物':5007,'数码电器':5009,'美容美发':5006,'医疗健康':5007}
    if source_category in narrow:return narrow[source_category],'支付宝明确业务分类；保留商品证据，可人工修订'
    return 5016,'已确认消费；商品和商户用途仍不足'

def inherit_refund_categories(events):
    byid={e['id']:e for e in events}
    for e in events:
        original=byid.get(e.get('related_id'))
        if e.get('link_type')!='退款对应原消费' or e.get('expense_minor',0)>=0 or not original:continue
        if original.get('code') not in EXPENSES:continue
        e['code']=original['code']
        e['reason']=e.get('reason','')+'；退款沿用原单当前费用类别'
        if e['code']==5016:e['review']='科目待补'
        elif e.get('review')=='科目待补':e['review']='自动通过'

def parse_date(raw):
    if not raw:
        return None
    s = str(raw)
    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
        return s[:10]
    if re.fullmatch(r'\d{8}', s):
        return datetime.strptime(s, '%Y%m%d').date().isoformat()
    return None

def describe(r):
    f = json.loads(r['raw_fields_json'])
    cp = f.get('对手信息') or f.get('对方户名') or f.get('交易对方') or ''
    return ' / '.join(x for x in [r['description_raw'] or '',cp] if x), f

def normalize(r, account_last4_overrides=None):
    text, fields = describe(r)
    family = r['source_family']
    tx = parse_date(r['transaction_date_iso']); post = parse_date(r['posting_date_iso'])
    business_date = tx or post
    date_basis = '原始交易日期' if tx else ('银行入账日期' if post else '日期待确认')
    # A CMB installment's MM/DD often denotes the original loan, not this posting.
    special = re.search(r'分期|利息|贴息|还款',text) or (r['amount_minor'] < 0)
    if family == 'cmb_credit' and not tx and post and not special:
        mmdd = re.fullmatch(r'(\d{2})/(\d{2})', r['transaction_date_raw'] or '')
        if mmdd:
            p = date.fromisoformat(post)
            candidates=[]
            for year in [p.year-1,p.year]:
                try:
                    candidate=date(year,int(mmdd[1]),int(mmdd[2]))
                    if 0 <= (p-candidate).days <= 45: candidates.append(candidate)
                except ValueError: pass
            if len(candidates)==1:
                business_date=candidates[0].isoformat();date_basis='月日+入账年唯一匹配（45日内）'
    if special and post and 'credit' in family:
        business_date=post;date_basis='银行入账日期（分期/还款/贷记）'
    rawaccount = str(r['account_identifier_raw'] or '')
    last4 = re.sub(r'\D','',rawaccount)[-4:]
    if family in (account_last4_overrides or {}):
        last4 = str(account_last4_overrides[family])
        if not re.fullmatch(r'\d{4}', last4):
            raise ValueError('Configured account suffix must contain exactly four digits.')
    account=LABELS[family]+('·'+last4 if last4 else '')
    return dict(id=r['transaction_row_id'],file_id=r['file_id'],source=r['filename'],locator=r['source_locator'],
        family=family,account=account,account_last4=last4,date=business_date,date_basis=date_basis,
        posting_date=post,description=text,raw_amount_minor=r['amount_minor'],amount_minor=abs(r['amount_minor']),
        source_status=r['event_status_raw'],event='pending',code=None,behavior='待确认',review='待确认',
        reason='',income_minor=0,expense_minor=0,cash_minor=r['amount_minor'] if family.endswith('_debit') else 0,
        cash_type='待确认收付' if family.endswith('_debit') else '非现金来源',cash_included=int(family.endswith('_debit')),
        debt_delta_minor=None,related_id=None,link_type=None,in_scope=int(bool(business_date and business_date.startswith('2026'))),
        reimb='未确认',rule_version=VERSION,product_description='',source_category='',category_evidence='',fields=fields)

def decide(e, payroll_ids, cfg):
    t=e['description'];a=e['raw_amount_minor'];fam=e['family'];f=e['fields']
    def setv(event,code=None,reason='来源事件规则',review='自动通过'):
        e.update(event=event,code=code,reason=reason,review=review)
    if fam=='jd_baitiao':
        setv('日期或退款时点待确认',None,'原订单及退款状态保留；不按当前年补日期','待确认');return
    if fam=='alipay':
        if '还款成功'==e['source_status']:
            setv('信用还款',2001,'花呗还款成功；银行现金须关联实扣');e['debt_delta_minor']=-abs(a);return
        if e['source_status']=='交易关闭':
            setv('关闭订单',None,'订单关闭，退款可能为同一订单的取消结算');return
        if e['source_status']=='退款成功':
            setv('退款待关联',None,'须与原订单闭环，避免关闭订单再计负支出','待确认');return
        if e['source_status']=='交易成功':
            code,reason=classify_category(t);setv('消费',code,reason);e['expense_minor']=abs(a);e['debt_delta_minor']=abs(a)
    elif fam.endswith('_credit'):
        if '分期退货-' in t:
            setv('分期退款结算调整',2001,'分期未出账本金回转；商户退款独立计费用冲回');return
        if re.search(r'银联入账|银联代付|溢缴款',t):
            setv('信用账户来款待确认',None,'不能仅由贷记符号判断为消费退款','待确认');return
        if re.search(r'本金.*第|分期本金|每月分摊本金',t):
            setv('分期本金出账',2001,'已购商品/存量融资本金的账单流转，不重复确认消费');return
        if re.search(r'分期利息|分摊利息|财政贴息|利息减免|循环利息|^利息',t):
            setv('利息或贴息',5012);e['expense_minor']=a;e['debt_delta_minor']=a
        elif re.search(r'转分期|分期办理|全账分期转出',t) and a<0:
            setv('转分期',2002,'结算债务转入分期；不记收入或负消费');return
        elif re.search(r'还款|移动银行|网银转入|自助转入',t) and a<0:
            setv('信用还款',2001);e['debt_delta_minor']=a;return
        elif '手续费' in t or '年费' in t:
            setv('手续费',5013);e['expense_minor']=a;e['debt_delta_minor']=a
        elif not t.strip() or (a<0 and re.search(r'调整|其他入账',t)):
            setv('性质待确认',None,'金额可靠但交易性质缺失','待确认');return
        else:
            code,reason=classify_category(t)
            setv('消费' if a>=0 else '消费退款或返利',code,reason)
            e['expense_minor']=a;e['debt_delta_minor']=a
            if a<0:e['reason']+='；按银行贷记日期，原单关联另行校验'
    else:
        if re.search(r'个贷还款',t):
            setv('房贷还款待拆分',2003,'实扣确定；本金与利息未提供拆分，不推造费用','待确认');e['cash_type']='房贷付款（本息未拆）';return
        if '银证转账' in t:
            setv('证券资金划转',None,'仅现金划转；不等于投资买卖或收益');e['cash_type']='证券账户划转';return
        if e['id'] in payroll_ids:
            setv('工资实收',4001,'已知雇主、工资节奏及原薪酬资料；含奖金时不拆分');e['income_minor']=a;e['cash_type']='工资实收'
            if e['id'] in cfg.get('income_review_ids',[]):
                e.update(review='收入判断待确认',reason='保留本轮AI工资候选；日期或金额超出用户通常范围，请在收入确认页核实')
            return
        if cfg['employer_keyword'] in t:
            if cfg.get('small_employer_receipts_are_reimbursement') and 0<a<1000000:
                setv('报销回款',4005,'用户确认总额口径：公司几百/几千来款通常是报销；原支出保留费用，回款计其他业务收入');e.update(income_minor=a,cash_type='报销回款',reimb='报销回款（总额口径）');return
            setv('雇主其他来款',None,'可能报销或其他薪酬，待性质及原单确认','待确认');e['cash_type']='雇主其他来款待确认';return
        if re.search(r'结息|活期储蓄存款利息',t):
            setv('存款利息',4004);e['income_minor']=a;e['cash_type']='利息收入';return
        if re.search(r'信用卡还款|信贷业务待还款|^.* / 还款$',t) and a<0:
            setv('信用还款',2001);e['cash_type']='信用支付还款';return
        own=cfg['owner_name'] in t
        if own and (re.search(r'转账|汇款|转入|转出|代付',t)):
            setv('本人账户划转',1001,'对手为本人；仅已匹配双边的范围内划转抵消');e['cash_type']='本人账户划转待匹配';return
        if re.search(r'CRS存款|自助机具存款',t):
            setv('现金存入待溯源',None,'现金存入不是新增收入；现金来源/期初余额待确认','待确认');e['cash_type']='现金存入待溯源';return
        if re.search(r'微信转账|微信红包',t):
            setv('个人转付待确认',None,'转付或红包不能仅凭支付通道判断用途','待确认');return
        if re.search(r'思享驾',t):
            setv('车辆租赁付款',5004,'沿用已确认车辆月付用途，合同本金/服务拆分待合同');e['expense_minor']=-a;e['cash_type']='消费现金收付'
        elif a>0 and re.search(r'退款|冲补账',t):
            code,reason=classify_category(t);setv('消费退款',code,reason);e['expense_minor']=-a;e['cash_type']='消费现金收付'
        elif a<0 and re.search(r'支付|消费|骑行',t) and not re.search(r'辅助无密|肯特瑞小金库',t):
            code,reason=classify_category(t);setv('消费',code,reason);e['expense_minor']=-a;e['cash_type']='消费现金收付'
        else:
            setv('性质待确认',None,'对手/业务说明不足；现金已计入，暂不计收入费用','待确认');return
    if e['code']==5016:e['review']='科目待补';e['behavior']='待确认'
    elif e['expense_minor']!=0:e['behavior']='用途已识别，消费性质待确认'
    if re.search(r'嘀嘀|滴滴',t):e['reimb']='通勤/工作待区分';e['reason']+='；不将全部打车自动认作报销'

def link_events(events):
    links=[];byid={e['id']:e for e in events};used=set()
    # Internal cash movement: own-name evidence + exact amount/date + unique opposite.
    transfers=[e for e in events if e['event']=='本人账户划转']
    for e in transfers:
        if e['id'] in used or e['cash_minor']>=0:continue
        matches=[v for v in transfers if v['id'] not in used and v['cash_minor']==-e['cash_minor'] and v['date']==e['date'] and v['account']!=e['account']]
        if len(matches)==1:
            v=matches[0]
            reverse=[w for w in transfers if w['id'] not in used and w['cash_minor']==-v['cash_minor'] and w['date']==v['date'] and w['account']!=v['account']]
            if len(reverse)!=1:continue
            for x,y in [(e,v),(v,e)]:x.update(related_id=y['id'],link_type='范围内转账双边抵消',cash_included=0,cash_type='范围内转账（抵消）')
            used.update([e['id'],v['id']]);links.append([e['id'],v['id'],'内部转账','自动通过','本人对手；同日同币种等额；双向唯一'])
    # Huabei refund of a closed order: merchant order ID is the linking evidence.
    for e in events:
        if e['event']!='退款待关联':continue
        key=e['fields'].get('商家订单号')
        candidates=[v for v in events if v['event']=='关闭订单' and key and v['fields'].get('商家订单号')==key and v['amount_minor']==e['amount_minor']]
        successful=[v for v in events if v['family']=='alipay' and v['source_status']=='交易成功' and key and v['fields'].get('商家订单号')==key]
        if len(successful)==1 and e['amount_minor']<=successful[0]['amount_minor']:
            v=successful[0]
            e.update(event='消费退款',code=v['code'],expense_minor=-e['amount_minor'],debt_delta_minor=-e['amount_minor'],review='自动通过',related_id=v['id'],link_type='退款对应原消费',reason='原成功付款订单号与退款一致')
            links.append([e['id'],v['id'],'退款对应消费','自动通过','明确成功付款订单号'])
        elif len(candidates)==1 and not successful:
            v=candidates[0];e.update(event='取消订单退款',review='自动通过',related_id=v['id'],link_type='关闭订单退款',reason='相同商家订单号及金额；取消结算，不形成消费净额')
            v.update(related_id=e['id'],link_type='关闭订单退款')
            links.append([e['id'],v['id'],'取消订单闭环','自动通过','订单号+金额一致'])
    # Payment-side and credit-side evidence are linked; credit evidence never creates cash.
    used=set()
    allocated=defaultdict(int)
    for e in events:
        if e['link_type']=='退款对应原消费':allocated[e['related_id']]+=e['amount_minor']
    for e in sorted(events,key=lambda e:e['date'] or ''):
        if e['event']!='信用还款' or e['family'].endswith('_debit') or not e['date']:continue
        candidates=[]
        for v in events:
            if not v['family'].endswith('_debit') or v['cash_minor']!=-e['amount_minor'] or v['id'] in used or not v['date']:continue
            days=abs((date.fromisoformat(v['date'])-date.fromisoformat(e['date'])).days)
            if days>2:continue
            if e['family']=='alipay':
                method=e['fields'].get('收/付款方式','');strong=v['account_last4'] in method and days==0
            else:strong=v['event']=='信用还款' and ((e['family'].split('_')[0] in v['family']) or e['account_last4'] in str(v['fields'].get('对方账号','')))
            if strong:candidates.append(v)
        if len(candidates)==1:
            v=candidates[0];used.add(v['id']);e.update(related_id=v['id'],link_type='还款双源');v.update(related_id=e['id'],link_type='还款双源',event='信用还款',code=2001,expense_minor=0,review='自动通过',cash_type='信用支付还款',reason='现金实扣与信用还款双源唯一匹配')
            links.append([e['id'],v['id'],'还款双源','自动通过','明确还款/付款账户；唯一等额，最多2日'])
    # Actual refunds need not equal the original (partial refunds remain independent).
    for e in events:
        if '退款' not in e['event'] or not e['expense_minor']<0 or e['related_id']:continue
        norm=lambda s:re.sub(r'退款| / |快捷支付|快捷退款|银联快捷支付|冲补账处理|[\s－-]','',s)
        cs=[v for v in events if (v['expense_minor']>=-e['expense_minor'] or (v['event']=='报销垫付' and v['amount_minor']>=e['amount_minor'])) and v['account']==e['account'] and norm(v['description'])==norm(e['description']) and v['date'] and e['date'] and 0<=(date.fromisoformat(e['date'])-date.fromisoformat(v['date'])).days<=120]
        if len(cs)==1:
            v=cs[0]
            if allocated[v['id']]+e['amount_minor']>v['amount_minor']:
                e.update(event='退款金额待核',review='待确认',reason='累计退款超出唯一原单金额；贷记事实保留，性质待确认',expense_minor=0,code=None)
            else:
                allocated[v['id']]+=e['amount_minor'];e.update(related_id=v['id'],link_type='退款对应原消费');links.append([e['id'],v['id'],'退款对应消费','自动通过','同账户商户、时序、唯一原单；累计不超过原单'])
    return links

def main():
    from source_enrichment import alipay_enrichments, merchant_matches
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);args=ap.parse_args()
    cfg=json.loads(Path(args.config).read_text());root=Path(cfg['project_dir']);out=root/'data';out.mkdir(exist_ok=True)
    source=Path(cfg['source_db']);sha_before=digest(source)
    inventory=json.loads((out/'source_inventory.json').read_text());raw=inventory['records']
    events=[normalize(r, cfg.get('account_last4_overrides')) for r in raw]
    payroll_ids=set(cfg.get('reviewed_payroll_ids',[]))
    assert payroll_ids<={e['id'] for e in events}
    for e in events:decide(e,payroll_ids,cfg)
    # Existing channel evidence enriches an already captured bank event only.
    # It never imports a second expense, cash movement or card liability.
    enrichment_path=out/'alipay_category_enrichment.json'
    enrichments=alipay_enrichments(inventory,events)
    enrichment_path.write_text(json.dumps(enrichments,ensure_ascii=False,indent=2))
    byid={e['id']:e for e in events};used_evidence=set();verified_files=set()
    for item in enrichments:
        e=byid[item['id']];record=item['evidence'];file_hash=item['source_sha256']
        if file_hash not in verified_files:
            sf=next(f for f in inventory['source_files'] if f['sha256']==file_hash)
            assert digest(sf['archived_path'])==file_hash,'Enrichment source changed'
            verified_files.add(file_hash)
        evidence_key=(file_hash,record['source_csv_line'])
        assert evidence_key not in used_evidence,'Channel evidence matched more than once'
        used_evidence.add(evidence_key)
        day_gap=abs((date.fromisoformat(e['date'])-date.fromisoformat(record['交易时间'][:10])).days)
        assert e['amount_minor']==record['minor'] and (day_gap==0 or day_gap<=2 and merchant_matches(e['description'],record.get('交易对方','')))
        assert e['account_last4'] and e['account_last4'] in record['收/付款方式']
        assert e['expense_minor']>0 and record['收/支']=='支出'
        product=record.get('商品说明','');category=record.get('交易分类','')
        code,basis=classify_purchase(e['description'],product,category)
        if code==5016 and e['code'] in EXPENSES and e['code']!=5016:
            code=e['code'];basis='商品说明不足以细分，保留既有业务用途类别'
        e.update(code=code,product_description=product,source_category=category,
                 category_evidence=f"{item['source_file']} 第{record['source_csv_line']}行；{item['match_basis']}",
                 review='自动通过' if code!=5016 else '科目待补',
                 reason=e['reason']+'；'+basis+'；商品：'+product,
                 behavior='用途已识别，消费性质待确认' if code!=5016 else '待确认')
        if '旧机款信用预付' in product:
            e.update(review='交易性质待核',reason=e['reason']+'；回收预付结算性质待核，本次仅补类别，不改既有金额')
    for e in events:
        if e['family']=='alipay' and e['expense_minor']>0:
            product=e['fields'].get('商品说明','');category=e['fields'].get('交易分类','')
            code,basis=classify_purchase(e['description'],product,category)
            e.update(code=code,product_description=product,source_category=category,category_evidence='本条原始支付宝商品说明',reason=e['reason']+'；'+basis,review='自动通过' if code!=5016 else '科目待补')
    # Supplemental purchase exports describe the same payments. Their order
    # totals and cumulative refund fields never create new expense/cash rows.
    purchase_references=None
    purchase_sources_path=out/'alipay_purchase_sources.json'
    if purchase_sources_path.exists():
        from purchase_reference_enrichment import purchase_reference_enrichments
        purchase_sources=json.loads(purchase_sources_path.read_text())
        purchase_references=purchase_reference_enrichments(purchase_sources,enrichments,events)
        (out/'alipay_purchase_reference_results.json').write_text(json.dumps(purchase_references,ensure_ascii=False,indent=2))
        for match in purchase_references['matches']:
            e=byid[match['id']];record=match['evidence'];product=record['商品名称'].strip()
            code,basis=classify_purchase(e['description'],product,e.get('source_category',''))
            if code==5016 and e['code'] in EXPENSES:code=e['code']
            refs=record.get('source_evidence',[record])
            evidence='；'.join(f"{r['source_file']} 第{r['source_row']}行" for r in refs)
            e.update(code=code,product_description=product or e['product_description'],
                     category_evidence=e['category_evidence']+'；'+evidence+'；'+match['match_basis'],
                     reason=e['reason']+'；补充买入明细：'+basis)
            if e['review'] in ['科目待补','自动通过']:
                e['review']='自动通过' if code!=5016 else '科目待补'
    for rule in cfg.get('category_defaults',[]):
        assert rule['code'] in EXPENSES and rule.get('source')
        for e in events:
            if e['expense_minor'] and e['code']==5016 and re.search(rule['pattern'],e['description'],re.I):
                e.update(code=rule['code'],review='自动通过',reason=e['reason']+'；'+rule['source'],behavior='用途已识别，消费性质待确认')
    # A repeated, consistent specialist merchant can supply a category estimate
    # for another payment. This is explicitly not an order-level association.
    merchant_profiles=defaultdict(list)
    for item in enrichments:
        party=item['evidence'].get('交易对方','')
        if len(party)>=6 and not re.search(r'天猫|淘宝|支付宝|小红书|阿里[（(]四川|平台|特约',party):
            merchant_profiles[party].append((byid[item['id']]['code'],item['evidence']['source_csv_line']))
    for e in events:
        if e['code']!=5016 or not e['expense_minor']:continue
        candidates=[(party,profile) for party,profile in merchant_profiles.items()
                    if party in e['description'] and len(profile)>=2 and len({x[0] for x in profile})==1 and profile[0][0]!=5016]
        if len(candidates)==1:
            party,profile=candidates[0];code=profile[0][0]
            e.update(code=code,review='自动通过',reason=e['reason']+f'；同商户{len(profile)}笔已匹配商品均属{COA[code]}，按商户用途推定；不声称匹配本笔订单',category_evidence='同商户商品参考；支付宝CSV行：'+','.join(str(x[1]) for x in profile))
    # Explicit user-confirmed pass-through groups remain cash facts, never P&L.
    bridge_links=[]
    byid={e['id']:e for e in events}
    for group in cfg.get('bridge_groups',[]):
        members=[byid[x] for x in group['ids']]
        assert all(e['family'].endswith('_debit') for e in members)
        assert sum(e['cash_minor'] for e in members)==0,'Pass-through group must reconcile'
        for e in members:
            e.update(event='过桥代收代付',code=2005,income_minor=0,expense_minor=0,cash_type='过桥代收代付',review='自动通过',reason=group['basis'])
        outgoing=[e for e in members if e['cash_minor']<0]
        if len(outgoing)==1:
            for e in members:
                if e['cash_minor']>0:
                    e.update(related_id=outgoing[0]['id'],link_type='过桥资金组')
                    bridge_links.append([e['id'],outgoing[0]['id'],'过桥资金组','用户确认',group['basis']+'；按本笔流入金额分配，组合计为零'])
    for e in events:
        if e['id'] in cfg.get('family_support_candidates',[]):
            e.update(behavior='家庭赞助候选',reason='用户说明部分现金存入为家庭赞助；具体存款逐笔待确认，不先计收入')
    # Bounded, user-confirmed context is supplied locally, never inferred globally.
    for rule in cfg.get('confirmed_rules',[]):
        matches=[e for e in events if e['date'] and e['date'].startswith(rule['period']) and re.search(rule['pattern'],e['description'],re.I) and (rule.get('amount_minor') is None or e['amount_minor']==rule['amount_minor']) and e['expense_minor']>0]
        if rule.get('group_total_minor') is not None and sum(e['amount_minor'] for e in matches)!=rule['group_total_minor']:
            continue
        for e in matches:
            for k in ['behavior','code','reimb']:
                if k in rule:e[k]=rule[k]
            e['reason']+='；'+rule['source']
            if rule.get('reimb')=='用户确认可报销':
                e['reason']+='；总额口径保留费用，不另确认报销应收资产'
    # An override is a replacement of current interpretation, never a new raw fact.
    overrides_path=out/'manual_overrides.json'
    overrides=json.loads(overrides_path.read_text()) if overrides_path.exists() else []
    byid={e['id']:e for e in events};audit=[]
    for o in overrides:
        assert o['id'] in byid and o.get('reason'),'Unknown override ID or missing reason'
        e=byid[o['id']];before={k:v for k,v in e.items() if k!='fields'}
        allowed={'code','behavior','reimb','event','expense_minor','income_minor','cash_type','date'}
        assert set(o['changes'])<=allowed
        if 'code' in o['changes']:assert o['changes']['code'] in COA or o['changes']['code'] is None
        for k,v in o['changes'].items():e[k]=v
        assert isinstance(e['expense_minor'],int) and isinstance(e['income_minor'],int)
        assert not (e['expense_minor'] and e['income_minor']),'Use a supported split event instead of two interpretations'
        assert abs(e['expense_minor'])<=e['amount_minor'] and abs(e['income_minor'])<=e['amount_minor']
        e['in_scope']=int(bool(e['date'] and e['date'].startswith('2026')))
        e.update(review=o.get('review','人工修订'),reason=o['reason']);audit.append((e['id'],json.dumps(before,ensure_ascii=False),json.dumps(o,ensure_ascii=False)))
    # Rebuild relationships AFTER all effective interpretation edits.
    links=link_events(events)+bridge_links
    for pair in cfg.get('confirmed_refund_links',[]):
        refund,original=byid[pair['refund_id']],byid[pair['original_id']]
        assert refund['account']==original['account'] and refund['date']>=original['date']
        assert refund['expense_minor']<0<original['expense_minor'] and abs(refund['expense_minor'])<=original['expense_minor']
        assert not refund['related_id'] or refund['related_id']==original['id']
        refund.update(related_id=original['id'],link_type='退款对应原消费')
        links=[r for r in links if not(r[0]==refund['id'] and r[2]=='退款对应原消费')]
        links.append([refund['id'],original['id'],'退款对应原消费','源余额与顺序核验',pair['basis']])
    allocated=defaultdict(int)
    for e in events:
        if e['link_type']=='退款对应原消费':allocated[e['related_id']]+=-e['expense_minor']
    assert all(a<=byid[k]['expense_minor'] for k,a in allocated.items()),'Refund allocation exceeds original expense'
    inherit_refund_categories(events)
    for e in events:
        original=byid.get(e['related_id'])
        if e['link_type']=='退款对应原消费' and original and original['reimb']=='用户确认可报销':
            e.update(code=original['code'],reimb='工作消费商户退款',reason='同账户唯一原单关联；商户退款冲减原工作消费费用，不计报销收入',review='自动通过')
    # Store the original database as evidence tables in a separate processed copy.
    dbpath=out/'finance_processed.sqlite3';tmp=out/'finance_processed.build.sqlite3'
    if tmp.exists():tmp.unlink()
    source_conn=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);db=sqlite3.connect(tmp);source_conn.backup(db);source_conn.close()
    cols={'id':'TEXT PRIMARY KEY','file_id':'TEXT','source':'TEXT','locator':'TEXT','family':'TEXT','account':'TEXT','account_last4':'TEXT','date':'TEXT','date_basis':'TEXT','posting_date':'TEXT','description':'TEXT','raw_amount_minor':'INTEGER','amount_minor':'INTEGER','source_status':'TEXT','event':'TEXT','code':'INTEGER','behavior':'TEXT','review':'TEXT','reason':'TEXT','income_minor':'INTEGER','expense_minor':'INTEGER','cash_minor':'INTEGER','cash_type':'TEXT','cash_included':'INTEGER','debt_delta_minor':'INTEGER','related_id':'TEXT','link_type':'TEXT','in_scope':'INTEGER','reimb':'TEXT','rule_version':'TEXT'}
    cols.update(product_description='TEXT',source_category='TEXT',category_evidence='TEXT')
    db.execute('CREATE TABLE processed_transactions ('+','.join(k+' '+v for k,v in cols.items())+')')
    db.executemany('INSERT INTO processed_transactions VALUES ('+','.join('?' for _ in cols)+')',[[e[k] for k in cols] for e in events])
    db.execute('CREATE TABLE processed_links(row_a TEXT,row_b TEXT,relation TEXT,status TEXT,evidence TEXT)');db.executemany('INSERT INTO processed_links VALUES (?,?,?,?,?)',links)
    db.execute('CREATE TABLE chart_of_accounts(code INTEGER PRIMARY KEY,name TEXT)');db.executemany('INSERT INTO chart_of_accounts VALUES (?,?)',COA.items())
    db.execute('CREATE TABLE interpretation_audit(row_id TEXT,before_json TEXT,override_json TEXT)');db.executemany('INSERT INTO interpretation_audit VALUES (?,?,?)',audit)
    if purchase_references is not None:
        db.execute('CREATE TABLE purchase_reference_orders(order_id TEXT PRIMARY KEY,record_json TEXT)')
        db.executemany('INSERT INTO purchase_reference_orders VALUES (?,?)',[(r['支付宝交易号'],json.dumps(r,ensure_ascii=False)) for r in purchase_references['records']])
        db.execute('CREATE TABLE purchase_reference_matches(event_id TEXT UNIQUE,order_id TEXT UNIQUE,basis TEXT)')
        db.executemany('INSERT INTO purchase_reference_matches VALUES (?,?,?)',[(r['id'],r['order_id'],r['match_basis']) for r in purchase_references['matches']])
    db.execute('CREATE TABLE ledger_lines(event_id TEXT,sequence INTEGER,account_code INTEGER,account_label TEXT,debit_minor INTEGER,credit_minor INTEGER,basis TEXT,PRIMARY KEY(event_id,sequence))')
    ledger=[]
    for e in events:
        # Only facts with a known two-sided accounting interpretation produce lines.
        expense=e['expense_minor'];income=e['income_minor']
        account=1001 if e['family'].endswith('_debit') else 2001
        pairs=[]
        if expense:pairs=[(e['code'],expense),(account,-expense)]
        elif income:pairs=[(1001,income),(e['code'],-income)]
        elif e['event'] in ['过桥代收代付','代管资金收付']:pairs=[(1001,e['cash_minor']),(2005,-e['cash_minor'])]
        elif e['event']=='信用还款' and e['family'].endswith('_debit'):
            pairs=[(2001,-e['cash_minor']),(1001,e['cash_minor'])]
        elif e['link_type']=='范围内转账双边抵消' and e['cash_minor']<0:
            pairs=[(1001,-e['cash_minor']),(1001,e['cash_minor'])]
        if pairs:
            for i,(code,signed) in enumerate(pairs):
                label=COA[code]
                if code==1001:label=e['account']
                if code==2001:label=e['account'] if not e['family'].endswith('_debit') else (byid[e['related_id']]['account'] if e['related_id'] else '信用支付待分账户')
                if e['link_type']=='范围内转账双边抵消' and signed>0:label=byid[e['related_id']]['account']
                ledger.append((e['id'],i+1,code,label,max(signed,0),max(-signed,0),'当前解释；期初未知时仅表示发生额'))
    db.executemany('INSERT INTO ledger_lines VALUES (?,?,?,?,?,?,?)',ledger)
    db.execute('CREATE VIEW cash_facts AS SELECT id,date,account,cash_minor,cash_type,cash_included,related_id,in_scope FROM processed_transactions WHERE family LIKE "%_debit"')
    db.execute('CREATE VIEW monthly_income_expense AS SELECT substr(date,1,7) month,code,SUM(income_minor) income_minor,SUM(expense_minor) expense_minor,COUNT(*) rows FROM processed_transactions WHERE in_scope=1 GROUP BY month,code')
    db.execute('CREATE VIEW review_queue AS SELECT * FROM processed_transactions WHERE review != "自动通过"')
    validations={
        'raw_count_preserved':db.execute('SELECT COUNT(*) FROM raw_transactions').fetchone()[0]==len(events),
        'unique_interpretation_per_source':len(events)==len({e['id'] for e in events}),
        'ledger_balanced':not db.execute('SELECT event_id FROM ledger_lines GROUP BY event_id HAVING SUM(debit_minor)!=SUM(credit_minor)').fetchall(),
        'no_installment_principal_expense':all(e['expense_minor']==0 for e in events if e['event']=='分期本金出账'),
        'no_credit_repayment_cash':all(e['cash_minor']==0 for e in events if e['event']=='信用还款' and not e['family'].endswith('_debit')),
        'internal_transfer_zero_sum':sum(e['cash_minor'] for e in events if e['link_type']=='范围内转账双边抵消')==0,
        'unknown_year_excluded':all(not e['in_scope'] for e in events if not e['date']),
        'gross_reimbursements_are_income':all(e['code']==4005 and e['income_minor']==e['amount_minor'] for e in events if e['event']=='报销回款'),
        'work_expenses_retained':all(e['expense_minor']==e['amount_minor'] and e['code'] in EXPENSES for e in events if e['reimb']=='用户确认可报销'),
        'no_reimbursement_asset_in_gross_mode':all(e['code']!=1004 for e in events),
        'raw_source_hash_unchanged':sha_before==digest(source),
        'sqlite_integrity':db.execute('PRAGMA integrity_check').fetchone()[0]=='ok',
        'foreign_keys':not db.execute('PRAGMA foreign_key_check').fetchall(),
    }
    assert all(validations.values()),validations
    manifest={'version':VERSION,'reimbursement_policy':'gross_income_and_expense','source_sha256':sha_before,'source_db':str(source),'processed_at':datetime.now().isoformat(timespec='seconds'),'report_year':2026,'budget_year':2027,'source_rows':len(events),'validation':validations,'event_counts':dict(Counter(e['event'] for e in events))}
    db.execute('CREATE TABLE processing_manifest(json TEXT)');db.execute('INSERT INTO processing_manifest VALUES (?)',(json.dumps(manifest,ensure_ascii=False),));db.commit();db.close();tmp.replace(dbpath)
    clean=[{k:v for k,v in e.items() if k!='fields'} for e in events]
    (out/'processed_data.json').write_text(json.dumps({'manifest':manifest,'events':clean,'links':links,'inventory':{k:v for k,v in inventory.items() if k!='records'}},ensure_ascii=False))
    with (out/'transaction_processing.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(cols));writer.writeheader();writer.writerows(clean)
    (out/'validation.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'processed_rows':len(events),'in_2026':sum(e['in_scope'] for e in events),'validation':validations,'events':manifest['event_counts']},ensure_ascii=False))

if __name__=='__main__':main()
