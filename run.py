import numpy as np
import spacy
import tensorflow as tf
import sys
import os
import argparse

from utils import load_glove_embeddings, to_categorical, convert_questions_to_word_ids, get_cleaned_text
from input_handler import get_input_from_csv

from models import EmbeddingLayer, BiRNN_EncodingLayer, AttentionLayer, SoftAlignmentLayer, ComparisonLayer, AggregationLayer

from keras.layers import Input
from keras.models import Model
from keras.optimizers import Adam
from keras.callbacks import ModelCheckpoint


tf.logging.set_verbosity(tf.logging.INFO)
FLAGS = None


def build_model(embedding_matrix, max_length, hidden_unit, n_classes, keep_prob, load_pretrained_model=False):
    """
    1. Get embedded vectors
    2. Attention
    3. Comparison
    4. Aggregation
    """
    vocab_size, embedding_size = embedding_matrix.shape
    dropout_rate = 1 - keep_prob

    a = Input(shape=(max_length, ), dtype='int32', name='words_1')
    b = Input(shape=(max_length, ), dtype='int32', name='words_2')

    embedding_layer = EmbeddingLayer(vocab_size, embedding_size, max_length, hidden_unit, init_weights=embedding_matrix, dropout=dropout_rate, nr_tune=5000)
    embedded_a = embedding_layer(a)
    embedded_b = embedding_layer(b)

    if FLAGS.encode:
        encoded_a = BiRNN_EncodingLayer(max_length, hidden_unit)(embedded_a)
        encoded_b = BiRNN_EncodingLayer(max_length, hidden_unit)(embedded_b)
        attention = AttentionLayer(max_length, hidden_unit, dropout=dropout_rate)(encoded_a, encoded_b)
    else:
        attention = AttentionLayer(max_length, hidden_unit, dropout=dropout_rate)(embedded_a, embedded_b)

    align_layer = SoftAlignmentLayer(max_length, hidden_unit)
    align_beta = align_layer(embedded_b, attention)                   # alignment for sentence a
    align_alpha = align_layer(embedded_a, attention, transpose=True)  # alignment for sentence b

    comp_layer = ComparisonLayer(max_length, hidden_unit, dropout=dropout_rate)
    comp_1 = comp_layer(embedded_a, align_beta)
    comp_2 = comp_layer(embedded_b, align_alpha)

    scores = AggregationLayer(hidden_unit, n_classes)(comp_1, comp_2)

    model = Model(inputs=[a, b], outputs=[scores])
    model.compile(optimizer=Adam(lr=FLAGS.learning_rate), loss='categorical_crossentropy', metrics=['accuracy'])

    if load_pretrained_model:
        if FLAGS.load_model is None:
            raise ValueError("You need to specify the model location by --load_model=[location]")
        model.load_weights(FLAGS.load_model)

    return model


def do_predict(X=None):
    pass


def do_eval(test_data):
    if FLAGS.load_model is None:
        raise ValueError("You need to specify the model location by --load_model=[location]")

    # Load Testing Data
    question_1, question_2, labels = get_input_from_csv(test_data)

    # Load Pre-trained Model
    if FLAGS.best_glove:
        import en_core_web_md
        nlp = en_core_web_md.load()  # load best-matching version for Glove
    else:
        nlp = spacy.load('en')
    embedding_matrix = load_glove_embeddings(nlp.vocab, n_unknown=FLAGS.num_unknown)  # shape=(1071074, 300)
    model = build_model(embedding_matrix, FLAGS.max_length, FLAGS.num_hidden, FLAGS.num_classes, FLAGS.keep_prob, load_pretrained_model=True)

    # Convert the "raw data" to word-ids format && convert "labels" to one-hot vectors
    q1_test, q2_test = convert_questions_to_word_ids(question_1, question_2, nlp, max_length=FLAGS.max_length, encode=FLAGS.encode, tree_truncate=FLAGS.tree_truncate)
    labels = to_categorical(np.asarray(labels, dtype='int32'))

    accuracy = model.evaluate([q1_test, q2_test], labels, batch_size=FLAGS.batch_size, verbose=1)
    # print("[*] ACCURACY OF TEST DATA: %.4f" % accuracy)


def train(input_file, batch_size, n_epochs, save_dir=None):
    # Stage 1: Read training data (csv) && Preprocessing them
    question_1, question_2, labels = get_input_from_csv(input_file)
    question_1 = get_cleaned_text(question_1)
    question_2 = get_cleaned_text(question_2)

    # Stage 2: Load Pre-trained embedding matrix (Using GLOVE here)
    if FLAGS.best_glove:
        import en_core_web_md
        nlp = en_core_web_md.load()  # load best-matching version for Glove
    else:
        nlp = spacy.load('en')
    embedding_matrix = load_glove_embeddings(nlp.vocab, n_unknown=FLAGS.num_unknown)  # shape=(1071074, 300)

    # Stage 3: Build Model
    load_pretrained_model = True if FLAGS.load_model is not None else False
    model = build_model(embedding_matrix, FLAGS.max_length, FLAGS.num_hidden, FLAGS.num_classes, FLAGS.keep_prob, load_pretrained_model=load_pretrained_model)

    # Stage 4: Convert the "raw data" to word-ids format && convert "labels" to one-hot vectors
    q1_train, q2_train = convert_questions_to_word_ids(question_1, question_2, nlp, max_length=FLAGS.max_length, encode=FLAGS.encode, tree_truncate=FLAGS.tree_truncate)
    labels = to_categorical(np.asarray(labels, dtype='int32'))

    # Stage 5: Training
    save_dir = save_dir if save_dir is not None else 'checkpoints'
    filepath = os.path.join(save_dir, "weights-{epoch:02d}-{val_acc:.2f}.hdf5")
    checkpoint = ModelCheckpoint(filepath, monitor='val_acc', verbose=1, save_best_only=True, mode='max')
    model.fit(
        x=[q1_train, q2_train],
        y=labels,
        batch_size=batch_size,
        epochs=n_epochs,
        validation_split=0.33,
        callbacks=[checkpoint],
        shuffle=True,
        verbose=FLAGS.verbose
    )


def run(_):
    if FLAGS.mode == 'train':
        train(FLAGS.input_data, FLAGS.batch_size, FLAGS.num_epochs)
    elif FLAGS.mode == 'eval':
        do_eval(FLAGS.test_data)
    elif FLAGS.mode == 'predict':
        do_predict()
    else:
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--num_epochs',
        type=int,
        default=10,
        help='Specify number of epochs'
    )
    parser.add_argument(
        '--batch_size',
        type=int,
        default=64,
        help='Specify number of batch size'
    )
    parser.add_argument(
        '--embedding_size',
        type=int,
        default=300,
        help='Specify embedding size'
    )
    parser.add_argument(
        '--max_length',
        type=int,
        default=100,
        help='Specify the max length of input sentence'
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=10,
        help='Specify seed for randomization'
    )
    parser.add_argument(
        '--input_data',
        type=str,
        default="./data/train.csv",
        help='Specify the location of input data',
    )
    parser.add_argument(
        '--test_data',
        type=str,
        default="./data/test.csv",
        help='Specify the location of test data',
    )
    parser.add_argument(
        '--num_classes',
        type=int,
        default=2,
        help='Specify the number of classes'
    )
    parser.add_argument(
        '--num_hidden',
        type=int,
        default=100,
        help='Specify the number of hidden units in each rnn cell'
    )
    parser.add_argument(
        '--num_unknown',
        type=int,
        default=100,
        help='Specify the number of unknown words for putting in the embedding matrix'
    )
    parser.add_argument(
        '--learning_rate',
        type=float,
        default=1e-4,
        help='Specify dropout rate'
    )
    parser.add_argument(
        '--keep_prob',
        type=float,
        default=0.8,
        help='Specify the rate (between 0 and 1) of the units that will keep during training'
    )
    parser.add_argument(
        '--mode',
        type=str,
        help='Specify mode: train or eval or predict',
        required=True
    )
    parser.add_argument(
        '--best_glove',
        action='store_true',
        help='Glove: using light version or best-matching version',
    )
    parser.add_argument(
        '--encode',
        action='store_true',
        help='If encode is assigned, sentence will pass through BiRNN-Encoding Layer',
        default=False
    )
    parser.add_argument(
        '--tree_truncate',
        action='store_true',
        help='Specify whether do tree_truncate or not',
        default=False
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose on training',
        default=False
    )
    parser.add_argument(
        '--load_model',
        type=str,
        help='Locate the path of the model',
    )

    FLAGS, unparsed = parser.parse_known_args()
    tf.app.run(main=run, argv=[sys.argv[0]] + unparsed)
