/** 评估页与对话页共用的危机横幅：醒目红底 + 12355 热线。 */
export default function CrisisBanner() {
  return (
    <div className="bg-red-50 border border-red-300 text-red-700 rounded-xl p-4">
      <div className="font-bold text-base mb-1">请立即寻求帮助</div>
      <p className="text-sm">
        你的感受很重要，你不是一个人。请立即联系你信任的老师、家长，
        或拨打青少年心理援助热线
        <span className="font-bold mx-1">12355</span>，
        专业人员会陪着你一起面对。
      </p>
    </div>
  )
}
