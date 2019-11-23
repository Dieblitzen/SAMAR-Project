import sys
sys.path.append('.')
from ImSeg_Dataset import ImSeg_Dataset
import ImSeg.network as network
import ImSeg.backbone as backbone

import os
import logging
import argparse
import numpy as np
import tensorflow as tf


"""
Calculate IoU, Precision and Recall per class for entire batch of images.
Requires:
  preds: model preds array, shape (batch, h, w, #c)
  label_masks: ground truth masks, shape (batch, h, w, #c)
  pred_threshold: Confidence threshold over which pixel prediction counted,
"""
def calculate_iou_prec_recall(preds, label_masks, pred_threshold=0.5):
  # Reduce dimensions across all but classes dimension.
  preds = preds.reshape(-1, preds.shape[-1])
  label_masks = label_masks.reshape(-1, label_masks.shape[-1])

  preds = preds > pred_threshold
  intersection = np.logical_and(preds, label_masks)
  union = np.logical_or(preds, label_masks)
  iou_scores = np.sum(intersection, axis=0) / np.sum(union, axis=0)
  iou_scores[np.isnan(iou_scores)] = 0.0

  precision = np.sum(intersection, axis=0)/np.sum(preds, axis=0)
  precision[np.isnan(precision)] = 0.0

  recall = np.sum(intersection, axis=0)/np.sum(label_masks, axis=0)
  recall[np.isnan(recall)] = 0.0

  return iou_scores, precision, recall


"""
Logs metrics to tensorflow summary writers, and to a log file.
Also prints mean metrics for the epoch
Requires:
  metrics_dict: Pairs of metric_name (make it informative!), metric_value_number
  writer: Either training or validation summary_writer
  phase: Either 'train' or 'val'
"""
def log_metrics(metrics_dict, writer, epoch, phase):
  # Do it separately for tf writers.
  with writer.as_default():
    for metric_name, metric_value in metrics_dict.items():
      tf.summary.scalar(metric_name, metric_value, step=epoch+1)

  print(f"Phase: {phase}")
  for metric_name, metric_value in metrics_dict.items():
    logging.info(f"Epoch {epoch+1}, Phase {phase}, {metric_name}: {metric_value}")
    
    if not metric_name.startswith('class'):
      print(f"{metric_name}: {metric_value}")


def passed_arguments():
  parser = argparse.ArgumentParser(description="Script to train an Image Segmentation model.")
  parser.add_argument('--data_path',
                      type=str,
                      required=True,
                      help='Path to directory where extracted dataset is stored.')
  parser.add_argument('--model_name',
                      type=str,
                      required=True,
                      help='Short name of model being trained. eg: refine_net_res50_pretrained')
  parser.add_argument('--classes_path',\
                      type=str,
                      default='./classes.json',
                      help='Path to directory where extracted dataset is stored.')
  parser.add_argument('--epochs',
                      type=int,
                      default=100,
                      help='Number of epochs to train the model.')
  parser.add_argument('--batch_size',
                      type=int,
                      default=32,
                      help='Size of batches to feed into model.')
  args = parser.parse_args()
  return args


"""
Performs one training step over a batch.
Passes one batch of images through the model, and backprops the gradients.
"""
@tf.function
def train_step(model, loss_function, train_loss, optimizer, images, labels):
  with tf.GradientTape() as tape:
    preds = model(images)
    loss = loss_function(labels, preds)

  gradients = tape.gradient(loss, model.trainable_variables)
  optimizer.apply_gradients(zip(gradients, model.trainable_variables))

  train_loss(loss)

  return preds

"""
Performs one validation step over a batch.
"""
@tf.function
def val_step(model, loss_function, val_loss, optimizer, images, labels):
  preds = model(images)
  loss = loss_function(labels, preds)

  val_loss(loss)

  return preds


if __name__ == "__main__":
  args = passed_arguments()

  epochs = args.epochs
  batch_size = args.batch_size
  model_name = args.model_name

  ## Set up dataset, number of training/validation samples and number of batches
  dataset = ImSeg_Dataset(data_path=args.data_path, classes_path=args.classes_path)
  img_size = dataset.image_size
  num_classes = len(dataset.seg_classes)
  num_train, num_val = dataset.data_sizes[0], dataset.data_sizes[1]
  num_train_batches, num_val_batches = num_train//batch_size, num_val//batch_size

  ## Summary writers for training/validation
  dataset.create_model_out_dir(model_name)
  train_summary_writer = tf.summary.create_file_writer(os.path.join(dataset.metrics_path, 'train'))
  val_summary_writer = tf.summary.create_file_writer(os.path.join(dataset.metrics_path, 'val'))
  # Set up Logger
  logging.basicConfig(filename=os.path.join(dataset.metrics_path, f"{model_name}.log"), level=logging.INFO)


  ## BEGIN: REFACTOR THIS CODE FOR BETTER MODEL LOADING
  backbone_model = backbone.resnet50()

  model = network.create_refine_net(backbone_model, [['layer3', 'layer4']], num_classes, input_shape=img_size)
  ## END: REFACTOR CODE 

  ## Loss and optimizer
  loss_function = tf.keras.losses.BinaryCrossentropy(from_logits=True)
  optimizer = tf.keras.optimizers.Adam(learning_rate=0.001)

  train_loss = tf.keras.metrics.Mean(name='train_loss')
  val_loss = tf.keras.metrics.Mean(name='val_loss')

  ## =============================================================================================
  ## BEGIN ITERATING OVER EPOCHS
  ## =============================================================================================

  best_val_loss = float('inf')
  for epoch in range(epochs):
    print(f"\nEpoch {epoch+1}")

    train_indices, val_indices = list(range(num_train)), list(range(num_val))
    np.random.shuffle(train_indices)
    np.random.shuffle(val_indices)
    
    # Alternate between training and validation epochs.
    for phase in ["train", "val"]:

      if phase == "train":
        num_batches, indices = num_train_batches, train_indices
        writer = train_summary_writer
        epoch_loss = train_loss 
        feed_model = train_step 
      else:
        num_batches, indices = num_val_batches, val_indices
        writer = val_summary_writer
        epoch_loss = val_loss
        feed_model = val_step

      # Initialise non loss metrics
      epoch_ious = epoch_prec = epoch_recall = np.zeros((num_classes))

      # Actual train/val over all batches.
      for batch in range(num_batches):
        img_input, label_masks =\
          dataset.get_batch(indices[batch*batch_size : (batch+1)*batch_size], phase)
        
        # Feed inputs to model
        img_input = np.array(img_input, dtype=np.float32)
        preds = feed_model(model, loss_function, epoch_loss, optimizer, img_input, label_masks)
        
        # Get metrics
        preds = preds.numpy()
        ious, prec, recall = calculate_iou_prec_recall(preds, label_masks, pred_threshold=0.5)

        # Update epoch metrics
        epoch_ious += ious
        epoch_prec += prec
        epoch_recall += recall
      
      epoch_ious = epoch_ious/num_batches
      epoch_prec = epoch_prec/num_batches
      epoch_recall = epoch_recall/num_batches

      # Add loss to metrics 
      metrics_dict = {'epoch_loss':epoch_loss.result(), 
                      'mean_iou':np.mean(epoch_ious),
                      'mean_prec':np.mean(epoch_prec), 
                      'mean_recall':np.mean(epoch_recall)}

      # Break down IoU, precision and recall by class
      for i, class_name in enumerate(dataset.seg_classes):
        sub_metric_dict = {'iou':epoch_ious, 'prec':epoch_prec, 'recall':epoch_recall}
        for metric_type, metrics in sub_metric_dict.items():
          class_metric_name = f'class_{class_name}_{metric_type}'
          class_metric = metrics[i]
          metrics_dict[class_metric_name] = class_metric
      
      # Log metrics, print metrics, write metrics to summary_writer
      log_metrics(metrics_dict, writer, epoch, phase)

      # Checkpoint model weights if loss is good
      if phase == 'val' and epoch_loss.result() < best_val_loss:
        best_val_loss = epoch_loss.result()
        model.save_weights(os.path.join(dataset.model_path, model_name))

    # End of epoch, reset loss.
    train_loss.reset_states()
    val_loss.reset_states()
    print("\n")

