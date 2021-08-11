# -*- coding: utf-8 -*-
"""“baseline.ipynb”的副本

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1aVTa1SqPmEHnQj564SvOmvZiOhgFOKOW
"""

import warnings
warnings.simplefilter('ignore')

import gc

import numpy as np
import pandas as pd
pd.set_option('max_columns', 100)
pd.set_option('max_rows', 100)
from tqdm.notebook import tqdm

from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_squared_error

import lightgbm as lgb

train = pd.read_csv('raw_data/train.csv')
train = train.sort_values(by=['QUEUE_ID', 'DOTTING_TIME']).reset_index(drop=True)

test = pd.read_csv('raw_data/evaluation_public.csv')
test = test.sort_values(by=['ID', 'DOTTING_TIME']).reset_index(drop=True)

sub_sample = pd.read_csv('raw_data/submit_example.csv')

train.head(10)

test.head(10)

sub_sample.head()

# 这些 columns 在 test 只有单一值, 所以直接去掉

del train['STATUS']
del train['PLATFORM']
del train['RESOURCE_TYPE']

del test['STATUS']
del test['PLATFORM']
del test['RESOURCE_TYPE']

# 时间排序好后也没什么用了

del train['DOTTING_TIME']
del test['DOTTING_TIME']

# Label Encoding

le = LabelEncoder()
train['QUEUE_TYPE'] = le.fit_transform(train['QUEUE_TYPE'].astype(str))
test['QUEUE_TYPE'] = le.transform(test['QUEUE_TYPE'].astype(str))

# 加个 id 后面方便处理
train['myid'] = train.index
test['myid'] = test.index

# 生成 target 列

df_train = pd.DataFrame()

for id_ in tqdm(train.QUEUE_ID.unique()):
    tmp = train[train.QUEUE_ID == id_]
    tmp['CPU_USAGE_next25mins'] = tmp['CPU_USAGE'].shift(-5)
    tmp['LAUNCHING_JOB_NUMS_next25mins'] = tmp['LAUNCHING_JOB_NUMS'].shift(-5)
    df_train = df_train.append(tmp)

df_train = df_train[df_train.CPU_USAGE_next25mins.notna()]
# df_train['CPU_USAGE_next25mins'] /= 100

print(df_train.shape)
df_train.head()

def run_lgb(df_train, df_test, target):
    
    feature_names = list(
        filter(lambda x: x not in ['CPU_USAGE_next25mins', 'LAUNCHING_JOB_NUMS_next25mins', 'QUEUE_ID', 'myid'], df_train.columns))
    
    model = lgb.LGBMRegressor(num_leaves=32,
                              max_depth=6,
                              learning_rate=0.08,
                              n_estimators=10000,
                              subsample=0.8,
                              feature_fraction=0.8,
                              reg_alpha=0.5,
                              reg_lambda=0.8,
                              random_state=2020)
    oof = []
    prediction = df_test[['ID', 'QUEUE_ID', 'myid']]
    prediction[target] = 0
    df_importance_list = []
    
    kfold = GroupKFold(n_splits=5)
    for fold_id, (trn_idx, val_idx) in enumerate(kfold.split(df_train, df_train[target], df_train['QUEUE_ID'])):
        
        X_train = df_train.iloc[trn_idx][feature_names]
        Y_train = df_train.iloc[trn_idx][target]
        X_val = df_train.iloc[val_idx][feature_names]
        Y_val = df_train.iloc[val_idx][target]
        
        print('\nFold_{} Training ================================\n'.format(fold_id+1))
        lgb_model = model.fit(X_train, 
                              Y_train,
                              eval_names=['train', 'valid'],
                              eval_set=[(X_train, Y_train), (X_val, Y_val)],
                              verbose=10,
                              eval_metric='mse',
                              early_stopping_rounds=20)
        
        pred_val = lgb_model.predict(X_val, num_iteration=lgb_model.best_iteration_)
        df_oof = df_train.iloc[val_idx][[target, 'myid', 'QUEUE_ID']].copy()
        df_oof['pred'] = pred_val
        oof.append(df_oof)
        
        pred_test = lgb_model.predict(df_test[feature_names], num_iteration=lgb_model.best_iteration_)
        prediction[target] += pred_test / kfold.n_splits
        
        df_importance = pd.DataFrame({
            'column': feature_names,
            'importance': lgb_model.feature_importances_,
        })
        df_importance_list.append(df_importance)
        
        del lgb_model, pred_val, pred_test, X_train, Y_train, X_val, Y_val
        gc.collect()
        
    return oof, prediction, df_importance_list

oof1, prediction1, df_importance_list1 = run_lgb(df_train, test, target='CPU_USAGE_next25mins')

df_importance1 = pd.concat(df_importance_list1)
df_importance1 = df_importance1.groupby(['column'])['importance'].agg(
    'mean').sort_values(ascending=False).reset_index()
df_importance1

df_oof1 = pd.concat(oof1)
score = mean_squared_error(df_oof1['CPU_USAGE_next25mins'], df_oof1['pred'])
print('MSE:', score)

prediction1.CPU_USAGE_next25mins.describe()

oof2, prediction2, df_importance_list2 = run_lgb(df_train, test, target='LAUNCHING_JOB_NUMS_next25mins')

df_importance2 = pd.concat(df_importance_list2)
df_importance2 = df_importance2.groupby(['column'])['importance'].agg(
    'mean').sort_values(ascending=False).reset_index()
df_importance2

df_oof2 = pd.concat(oof2)
score = mean_squared_error(df_oof2['LAUNCHING_JOB_NUMS_next25mins'], df_oof2['pred'])
print('MSE:', score)

prediction2.LAUNCHING_JOB_NUMS_next25mins.describe()

sub_sample.head()

prediction = prediction1.copy()
prediction = pd.merge(prediction, prediction2[['myid', 'LAUNCHING_JOB_NUMS_next25mins']], on='myid')

prediction.head(10)

# 注意: 提交要求预测结果需为非负整数

prediction['CPU_USAGE_next25mins'] = prediction['CPU_USAGE_next25mins'].apply(np.floor)
prediction['CPU_USAGE_next25mins'] = prediction['CPU_USAGE_next25mins'].apply(lambda x: 0 if x<0 else x)
prediction['CPU_USAGE_next25mins'] = prediction['CPU_USAGE_next25mins'].astype(int)
prediction['LAUNCHING_JOB_NUMS_next25mins'] = prediction['LAUNCHING_JOB_NUMS_next25mins'].apply(np.floor)
prediction['LAUNCHING_JOB_NUMS_next25mins'] = prediction['LAUNCHING_JOB_NUMS_next25mins'].apply(lambda x: 0 if x<0 else x)
prediction['LAUNCHING_JOB_NUMS_next25mins'] = prediction['LAUNCHING_JOB_NUMS_next25mins'].astype(int)

prediction.head(10)

sub_sample.head()

preds = []

for id_ in tqdm(prediction.ID.unique()):
    items = [id_]
    tmp = prediction[prediction.ID == id_].sort_values(by='myid').reset_index(drop=True)
    for i, row in tmp.iterrows():
        items.append(row['CPU_USAGE_next25mins'])
        items.append(row['LAUNCHING_JOB_NUMS_next25mins'])
    preds.append(items)

sub = pd.DataFrame(preds)
sub.columns = sub_sample.columns

sub.head(10)

sub.shape, sub_sample.shape

sub.to_csv('baseline_202010141435.csv', index=False)

