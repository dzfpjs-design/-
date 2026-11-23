import React from 'react';

export default function CandidateObservationScopeSummary({scope}) {
  if (!scope) return null;
  if (scope.check !== 'requested_observation_field_presence_only' ||
      !['matched', 'pending'].includes(scope.status) || !Array.isArray(scope.object_names) ||
      scope.object_names.some(name => typeof name !== 'string' || !name)) {
    return <p role="alert">补充观测字段适用范围无法核对，请重新检查配置。</p>;
  }
  if (scope.status === 'pending') return <p>补充观测字段的对象适用范围待核。</p>;
  if (!scope.object_names.length) return <p role="alert">补充观测字段适用范围无法核对，请重新检查配置。</p>;
  return <p>补充观测所需字段存在于：{scope.object_names.join('、')}。这里只核对字段是否存在；同一任务选择多个对象时，还需核对各对象的字段类型、单位和实际观测能力。</p>;
}
