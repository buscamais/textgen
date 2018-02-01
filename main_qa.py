import logging
import os

from loader.book_corpus import BookCorpusDataset
from loader.preprocess import preprocess_data_vocab, preprocess_simpleqa, split_simple_questions
from test.test import test
#from train.train import train
from train.train_with_kenlm import train
from train.network import Network
from utils.parser import parser
from utils.utils import Config, set_logger, prepare_paths

log = logging.getLogger('main')

if __name__ == '__main__':
    # Parsing arguments and set configs
    args = parser.parse_args()
    cfg = Config(vars(args))

    # Set all the paths
    prepare_paths(cfg)

    # Logger
    set_logger(cfg)
    log = logging.getLogger('main')

    # split simple_questions dataset file
    split_simple_questions()

    # Preprocessing
    preprocess_simpleqa(cfg)
    vocab = preprocess_vocab(cfg)

    # Load dataset
    train_q_data = BookCorpusDataset(cfg.train_q_data_filepath)
    train_a_data = BookCorpusDataset(cfg.train_a_data_filepath)
    # Build network
    net = Network(cfg, train_q_data, train_a_data, vocab)

    # Train
    if not cfg.test:
        train(net)
    # Test
    else:
        test(net)

    log.info('End of program.')
